# SPDX-License-Identifier: AGPL-3.0-only
"""Shared FastAPI dependencies: container access, current user, admin guard, cookies."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response, status

from flashcards.api.container import Container
from flashcards.application.auth_service import DEFAULT_SESSION_TTL
from flashcards.config import Settings
from flashcards.domain.invites import Role
from flashcards.domain.sessions import Session
from flashcards.domain.users import User, UserStatus

SESSION_COOKIE = "fc_session"


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user: User
    session: Session


_NOT_AUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
    headers={"WWW-Authenticate": "cookie"},
)


async def current_user(
    request: Request, container: Container = Depends(get_container)
) -> CurrentUser:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if raw_token is None:
        raise _NOT_AUTHENTICATED
    session = await container.auth.authenticate(raw_token)
    if session is None:
        raise _NOT_AUTHENTICATED
    user = await container.user_repo.get(session.user_id)
    if user is None:  # pragma: no cover - a live session always points to a live user
        raise _NOT_AUTHENTICATED
    if user.status() is UserStatus.SUSPENDED:
        raise _NOT_AUTHENTICATED
    return CurrentUser(user=user, session=session)


def require_admin(caller: CurrentUser = Depends(current_user)) -> CurrentUser:
    if caller.user.role is not Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only.")
    return caller


def set_session_cookie(response: Response, raw_token: str, settings: Settings) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=raw_token,
        max_age=int(DEFAULT_SESSION_TTL.total_seconds()),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
