# SPDX-License-Identifier: AGPL-3.0-only
"""Authentication routes: signup, login, logout, current user, sessions, email
verification and password reset (F-AUTH-1, F-AUTH-2)."""

from __future__ import annotations

import logging
import math

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse

from flashcards.api.container import Container
from flashcards.api.deps import (
    CurrentUser,
    clear_session_cookie,
    current_user,
    get_container,
    get_settings,
    set_session_cookie,
)
from flashcards.api.schemas import (
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    SessionOut,
    SignupRequest,
    TokenRequest,
    UserOut,
)
from flashcards.application.auth_service import AccountSuspended, InvalidCredentials
from flashcards.config import Settings
from flashcards.domain.invites import InviteError
from flashcards.domain.login_throttle import LoginLocked
from flashcards.domain.mail import OutgoingEmail
from flashcards.domain.ports import Mailer
from flashcards.domain.user_tokens import InvalidUserToken
from flashcards.domain.users import EmailAlreadyRegistered, InvalidEmail, WeakPassword

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_LINK = "This link is invalid or has expired."


def _send_later(tasks: BackgroundTasks, mailer: Mailer, email: OutgoingEmail | None) -> None:
    """Send after the response (and after the commit), never failing the request.

    Sending later also keeps a request's timing the same whether or not an email
    goes out, so it says nothing about whether an account exists.
    """
    if email is not None:
        tasks.add_task(_send_quietly, mailer, email)


async def _send_quietly(mailer: Mailer, email: OutgoingEmail) -> None:
    try:
        await mailer.send(email)
    except Exception:  # a mail outage must not surface as a server error
        logger.exception("could not send email: %s", email.subject)


@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def signup(
    body: SignupRequest,
    response: Response,
    tasks: BackgroundTasks,
    container: Container = Depends(get_container, scope="function"),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    try:
        user = await container.registration.register(
            raw_token=body.token, email=body.email, password=body.password
        )
    except (WeakPassword, InvalidEmail) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except EmailAlreadyRegistered as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "That email is not available.") from exc
    except InviteError as exc:
        # One generic answer for every invite failure, so the response never says
        # whether a token was unknown, expired, revoked or already used.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This invite is not valid.") from exc

    issued = await container.auth.start_session(user)
    set_session_cookie(response, issued.raw_token, settings)
    _send_later(tasks, container.mailer, await container.account_email.verification_email(user))
    return UserOut.from_user(user)


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
    response: Response,
    container: Container = Depends(get_container, scope="function"),
    settings: Settings = Depends(get_settings),
) -> UserOut | JSONResponse:
    try:
        issued = await container.sign_in.login(email=body.email, password=body.password)
    except InvalidCredentials:
        # Returned, not raised: in SQL mode a raised error rolls back the request's
        # transaction, which would discard the failed-attempt count and defeat the
        # lockout.
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid email or password."},
        )
    except LoginLocked as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed sign-in attempts. Try again later.",
            # Rounded up, so a client that waits exactly this long is never early.
            headers={"Retry-After": str(max(1, math.ceil(exc.retry_after.total_seconds())))},
        ) from exc
    except AccountSuspended as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is suspended.") from exc

    user = await container.user_repo.get(issued.session.user_id)
    if user is None:  # pragma: no cover - login just authenticated this user
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")
    set_session_cookie(response, issued.raw_token, settings)
    return UserOut.from_user(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    container: Container = Depends(get_container, scope="function"),
    settings: Settings = Depends(get_settings),
    caller: CurrentUser = Depends(current_user),
) -> Response:
    await container.auth.revoke_session(caller.session.id)
    clear_session_cookie(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
async def me(caller: CurrentUser = Depends(current_user)) -> UserOut:
    return UserOut.from_user(caller.user)


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> list[SessionOut]:
    return [
        SessionOut.from_session(s, current=s.id == caller.session.id)
        for s in await container.auth.sessions_for(caller.user.id)
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    owned = {s.id for s in await container.auth.sessions_for(caller.user.id)}
    if session_id not in owned or not await container.auth.revoke_session(session_id):
        # Unknown to this user, or already revoked: either way nothing to revoke.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such session.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/verify-email", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(
    body: TokenRequest,
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    try:
        await container.account_email.verify_email(body.token)
    except InvalidUserToken as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _INVALID_LINK) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/verify-email/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(
    tasks: BackgroundTasks,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    email = await container.account_email.verification_email(caller.user)
    _send_later(tasks, container.mailer, email)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/password-reset", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    body: PasswordResetRequest,
    tasks: BackgroundTasks,
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    # Always 202, whether or not the email has an account.
    email = await container.account_email.password_reset_email(body.email)
    _send_later(tasks, container.mailer, email)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_password_reset(
    body: PasswordResetConfirmRequest,
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    try:
        await container.account_email.reset_password(body.token, body.password)
    except WeakPassword as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except InvalidUserToken as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _INVALID_LINK) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
