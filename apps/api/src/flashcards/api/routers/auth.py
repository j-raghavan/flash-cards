# SPDX-License-Identifier: AGPL-3.0-only
"""Authentication routes: signup, login, logout, current user, sessions."""

from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException, Response, status
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
from flashcards.api.schemas import LoginRequest, SessionOut, SignupRequest, UserOut
from flashcards.application.auth_service import AccountSuspended, InvalidCredentials
from flashcards.config import Settings
from flashcards.domain.invites import InviteError
from flashcards.domain.login_throttle import LoginLocked
from flashcards.domain.users import EmailAlreadyRegistered, InvalidEmail, WeakPassword

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def signup(
    body: SignupRequest,
    response: Response,
    container: Container = Depends(get_container),
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
    return UserOut.from_user(user)


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
    response: Response,
    container: Container = Depends(get_container),
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
    container: Container = Depends(get_container),
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
    container: Container = Depends(get_container),
) -> list[SessionOut]:
    return [
        SessionOut.from_session(s, current=s.id == caller.session.id)
        for s in await container.auth.sessions_for(caller.user.id)
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> Response:
    owned = {s.id for s in await container.auth.sessions_for(caller.user.id)}
    if session_id not in owned or not await container.auth.revoke_session(session_id):
        # Unknown to this user, or already revoked: either way nothing to revoke.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such session.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
