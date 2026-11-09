# SPDX-License-Identifier: AGPL-3.0-only
"""Authentication routes: signup, login, logout, current user, sessions, email
verification, password reset and optional public sign-up (F-AUTH-1, F-AUTH-2,
F-AUTH-7)."""

from __future__ import annotations

import math

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
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
from flashcards.api.mail import send_later
from flashcards.api.schemas import (
    InstanceInfoOut,
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RegisterRequest,
    SessionOut,
    SessionsRevokedOut,
    SignupRequest,
    TokenRequest,
    UserOut,
)
from flashcards.application.auth_service import AccountSuspended, InvalidCredentials
from flashcards.config import Settings
from flashcards.domain.invites import InviteError
from flashcards.domain.login_throttle import LoginLocked
from flashcards.domain.sessions import device_label
from flashcards.domain.user_tokens import InvalidUserToken
from flashcards.domain.users import EmailAlreadyRegistered, InvalidEmail, WeakPassword

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_LINK = "This link is invalid or has expired."
_REGISTER_ACCEPTED = (
    "If the address can be registered, an email is on its way. Follow it, then sign in."
)


def _device(request: Request) -> str:
    """The device label for a new session; the raw User-Agent is not kept."""
    return device_label(request.headers.get("user-agent"))


def _retry_after(exc: LoginLocked) -> str:
    # Rounded up, so a client that waits exactly this long is never early.
    return str(max(1, math.ceil(exc.retry_after.total_seconds())))


@router.get("/options", response_model=InstanceInfoOut)
def auth_options(settings: Settings = Depends(get_settings)) -> InstanceInfoOut:
    """Whether sign-up needs an invite, so a client knows which form to show."""
    return InstanceInfoOut(invite_only=settings.invite_only)


@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def signup(
    body: SignupRequest,
    request: Request,
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

    issued = await container.auth.start_session(user, device=_device(request))
    set_session_cookie(response, issued.raw_token, settings)
    send_later(tasks, container.mailer, await container.account_email.verification_email(user))
    return UserOut.from_user(user)


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
async def register(
    body: RegisterRequest,
    request: Request,
    tasks: BackgroundTasks,
    container: Container = Depends(get_container, scope="function"),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Public sign-up, only when INVITE_ONLY is false (F-AUTH-7).

    Answers 202 whether or not the email already has an account, and does not
    sign in: the new account's owner follows the verification email, then signs
    in. Every outcome is returned rather than raised, so the attempt counted
    against the client is committed even when the request is refused.
    """
    if settings.invite_only:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    # Behind a reverse proxy this is the proxy's address unless the server trusts
    # its forwarded headers (uvicorn --proxy-headers --forwarded-allow-ips).
    client: str = getattr(request.client, "host", "unknown")
    try:
        outcome = await container.public_signup.register(
            email=body.email, password=body.password, client=client
        )
    except LoginLocked as exc:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Too many sign-up attempts. Try again later."},
            headers={"Retry-After": _retry_after(exc)},
        )
    except (WeakPassword, InvalidEmail) as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": str(exc)}
        )
    if outcome.created:
        email = await container.account_email.verification_email(outcome.user)
    else:
        email = container.account_email.account_exists_email(outcome.user)
    send_later(tasks, container.mailer, email)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED, content={"detail": _REGISTER_ACCEPTED}
    )


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    container: Container = Depends(get_container, scope="function"),
    settings: Settings = Depends(get_settings),
) -> UserOut | JSONResponse:
    try:
        issued = await container.sign_in.login(
            email=body.email, password=body.password, device=_device(request)
        )
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
            headers={"Retry-After": _retry_after(exc)},
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
    """The caller's live sessions (the device list), newest first."""
    return [
        SessionOut.from_session(s, current=s.id == caller.session.id)
        for s in await container.sessions.active_for(caller.user.id)
    ]


@router.post("/sessions/revoke-others", response_model=SessionsRevokedOut)
async def revoke_other_sessions(
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> SessionsRevokedOut:
    """Sign out every device except this one."""
    revoked = await container.sessions.revoke_others(
        user_id=caller.user.id, current_session_id=caller.session.id
    )
    return SessionsRevokedOut(revoked=revoked)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    if not await container.sessions.revoke(user_id=caller.user.id, session_id=session_id):
        # Unknown to this user, or no longer live: either way nothing to revoke.
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
    send_later(tasks, container.mailer, email)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/password-reset", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    body: PasswordResetRequest,
    tasks: BackgroundTasks,
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    # Always 202, whether or not the email has an account.
    email = await container.account_email.password_reset_email(body.email)
    send_later(tasks, container.mailer, email)
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
