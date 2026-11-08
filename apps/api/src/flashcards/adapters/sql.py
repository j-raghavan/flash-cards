# SPDX-License-Identifier: AGPL-3.0-only
"""SQLAlchemy async repository adapters bound to a single AsyncSession.

One session (and transaction) is shared by all three repositories within a
request, so a use case like registration commits the invite redemption and the
user insert together, or not at all.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Result, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flashcards.adapters.db import (
    CardRow,
    CardScheduleRow,
    DeckRow,
    InviteRow,
    NoteRow,
    SessionRow,
    UserRow,
    card_from_row,
    card_to_row,
    deck_from_row,
    deck_to_row,
    invite_from_row,
    invite_to_row,
    note_from_row,
    note_to_row,
    review_log_to_row,
    schedule_from_row,
    schedule_to_row,
    session_from_row,
    session_to_row,
    user_from_row,
    user_to_row,
)
from flashcards.domain.decks import Deck
from flashcards.domain.invites import Invite
from flashcards.domain.notes import Card, Note
from flashcards.domain.scheduling import CardSchedule, DuplicateReview, ReviewLog
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


class SqlDeckRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, deck: Deck) -> None:
        self._session.add(deck_to_row(deck))
        await self._session.flush()

    async def get(self, deck_id: str) -> Deck | None:
        row = await self._session.get(DeckRow, deck_id)
        return deck_from_row(row) if row is not None else None

    async def list_for_owner(self, owner_id: str) -> list[Deck]:
        rows = await self._session.scalars(
            select(DeckRow).where(DeckRow.owner_id == owner_id).order_by(DeckRow.created_at)
        )
        return [deck_from_row(row) for row in rows]

    async def update(self, deck: Deck) -> None:
        await self._session.merge(deck_to_row(deck))
        await self._session.flush()

    async def delete(self, deck_id: str) -> None:
        row = await self._session.get(DeckRow, deck_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()


class SqlNoteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, note: Note) -> None:
        self._session.add(note_to_row(note))
        await self._session.flush()

    async def get(self, note_id: str) -> Note | None:
        row = await self._session.get(NoteRow, note_id)
        return note_from_row(row) if row is not None else None

    async def delete(self, note_id: str) -> None:
        row = await self._session.get(NoteRow, note_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()


class SqlCardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, card: Card) -> None:
        self._session.add(card_to_row(card))
        await self._session.flush()

    async def get(self, card_id: str) -> Card | None:
        row = await self._session.get(CardRow, card_id)
        return card_from_row(row) if row is not None else None

    async def list_for_deck(self, deck_id: str) -> list[Card]:
        rows = await self._session.scalars(
            select(CardRow)
            .where(CardRow.deck_id == deck_id)
            .order_by(CardRow.created_at, CardRow.ordinal)
        )
        return [card_from_row(row) for row in rows]

    async def update(self, card: Card) -> None:
        await self._session.merge(card_to_row(card))
        await self._session.flush()

    async def delete_for_note(self, note_id: str) -> None:
        await self._session.execute(delete(CardRow).where(CardRow.note_id == note_id))


class SqlScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, card_id: str) -> CardSchedule | None:
        row = await self._session.get(CardScheduleRow, card_id)
        return schedule_from_row(row) if row is not None else None

    async def upsert(self, schedule: CardSchedule) -> None:
        await self._session.merge(schedule_to_row(schedule))
        await self._session.flush()


class SqlReviewLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, log: ReviewLog) -> None:
        if log.idempotency_key is None:
            self._session.add(review_log_to_row(log))
            await self._session.flush()
            return
        # A savepoint so a duplicate-key violation rolls back only this insert and
        # leaves the surrounding request transaction usable.
        try:
            async with self._session.begin_nested():
                self._session.add(review_log_to_row(log))
                await self._session.flush()
        except IntegrityError as exc:
            raise DuplicateReview(log.idempotency_key) from exc
