# SPDX-License-Identifier: AGPL-3.0-only
"""SQLAlchemy async repository adapters bound to a single AsyncSession.

One session (and transaction) is shared by all three repositories within a
request, so a use case like registration commits the invite redemption and the
user insert together, or not at all.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Result, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flashcards.adapters.db import (
    InviteRow,
    SessionRow,
    UserRow,
    invite_from_row,
    invite_to_row,
    session_from_row,
    session_to_row,
    user_from_row,
    user_to_row,
)
from flashcards.domain.invites import Invite
from flashcards.domain.sessions import Session
from flashcards.domain.users import EmailAlreadyRegistered, User


def _changed_one(result: Result[Any]) -> bool:
    """True when a DML statement affected exactly one row."""
    return cast("CursorResult[Any]", result).rowcount == 1


class SqlInviteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, invite: Invite) -> None:
        self._session.add(invite_to_row(invite))
        await self._session.flush()

    async def get(self, invite_id: str) -> Invite | None:
        row = await self._session.get(InviteRow, invite_id)
        return invite_from_row(row) if row is not None else None

    async def get_by_token_hash(self, token_hash: str) -> Invite | None:
        row = await self._session.scalar(
            select(InviteRow).where(InviteRow.token_hash == token_hash)
        )
        return invite_from_row(row) if row is not None else None

    async def list_all(self) -> list[Invite]:
        rows = await self._session.scalars(
            select(InviteRow).order_by(InviteRow.created_at.desc())
        )
        return [invite_from_row(row) for row in rows]

    async def try_redeem(self, invite_id: str, now: datetime) -> bool:
        result = await self._session.execute(
            update(InviteRow)
            .where(
                InviteRow.id == invite_id,
                InviteRow.redeemed_at.is_(None),
                InviteRow.revoked_at.is_(None),
            )
            .values(redeemed_at=now)
        )
        return _changed_one(result)

    async def try_revoke(self, invite_id: str, now: datetime) -> bool:
        result = await self._session.execute(
            update(InviteRow)
            .where(
                InviteRow.id == invite_id,
                InviteRow.redeemed_at.is_(None),
                InviteRow.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return _changed_one(result)


class SqlUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> None:
        self._session.add(user_to_row(user))
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise EmailAlreadyRegistered(user.email) from exc

    async def get(self, user_id: str) -> User | None:
        row = await self._session.get(UserRow, user_id)
        return user_from_row(row) if row is not None else None

    async def get_by_email(self, email: str) -> User | None:
        row = await self._session.scalar(select(UserRow).where(UserRow.email == email))
        return user_from_row(row) if row is not None else None


class SqlSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, session: Session) -> None:
        self._session.add(session_to_row(session))
        await self._session.flush()

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        row = await self._session.scalar(
            select(SessionRow).where(SessionRow.token_hash == token_hash)
        )
        return session_from_row(row) if row is not None else None

    async def list_for_user(self, user_id: str) -> list[Session]:
        rows = await self._session.scalars(
            select(SessionRow)
            .where(SessionRow.user_id == user_id)
            .order_by(SessionRow.created_at.desc())
        )
        return [session_from_row(row) for row in rows]

    async def revoke(self, session_id: str, now: datetime) -> bool:
        result = await self._session.execute(
            update(SessionRow)
            .where(SessionRow.id == session_id, SessionRow.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return _changed_one(result)
