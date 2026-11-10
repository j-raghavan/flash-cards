# SPDX-License-Identifier: AGPL-3.0-only
"""The shared library under concurrency, against a real Postgres (ADR 0024, needs Docker)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flashcards.adapters.db import Base, create_engine, create_sessionmaker
from flashcards.adapters.fsrs_scheduler import FsrsScheduler
from flashcards.adapters.sql import (
    SqlCardRepository,
    SqlDeckRepository,
    SqlMediaRepository,
    SqlMockExamRepository,
    SqlNoteRepository,
    SqlNoteTypeRepository,
    SqlPassageRepository,
    SqlReviewLogRepository,
    SqlScheduleRepository,
    SqlShareLinkRepository,
    SqlUserRepository,
)
from flashcards.application.deck_service import DeckService
from flashcards.application.library_service import LibraryService
from flashcards.application.note_service import NoteService
from flashcards.application.read_access import ReadAccess
from flashcards.application.review_service import ReviewService
from flashcards.domain.invites import Role
from flashcards.domain.notes import CardNotFound, NoteType
from flashcards.domain.scheduling import Rating
from flashcards.domain.users import User

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


class FakeClock:
    def now(self) -> datetime:
        return NOW


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


def _access(s: AsyncSession) -> ReadAccess:
    return ReadAccess(
        SqlUserRepository(s),
        SqlDeckRepository(s),
        SqlNoteRepository(s),
        SqlCardRepository(s),
        SqlNoteTypeRepository(s),
        SqlMediaRepository(s),
    )


def _reviews(s: AsyncSession, logs: SqlReviewLogRepository | None = None) -> ReviewService:
    return ReviewService(
        _access(s),
        SqlScheduleRepository(s),
        logs or SqlReviewLogRepository(s),
        FsrsScheduler(),
        FakeClock(),
    )


def _library(s: AsyncSession) -> LibraryService:
    clock = FakeClock()
    decks = DeckService(SqlDeckRepository(s), clock)
    notes = NoteService(decks, SqlNoteRepository(s), SqlCardRepository(s), clock)
    return LibraryService(
        decks,
        notes,
        SqlDeckRepository(s),
        SqlNoteRepository(s),
        SqlCardRepository(s),
        SqlScheduleRepository(s),
        SqlReviewLogRepository(s),
        SqlPassageRepository(s),
        SqlMockExamRepository(s),
        SqlShareLinkRepository(s),
    )


async def _seed(sm: async_sessionmaker[AsyncSession]) -> tuple[str, str]:
    """An admin's library deck with one card; returns (note id, card id)."""
    async with sm() as s, s.begin():
        await SqlUserRepository(s).add(
            User(
                id="admin", email="admin@e.com", password_hash="h", role=Role.ADMIN,
                created_at=NOW,
            )
        )
        decks = DeckService(SqlDeckRepository(s), FakeClock())
        notes = NoteService(decks, SqlNoteRepository(s), SqlCardRepository(s), FakeClock())
        deck = await decks.create(owner_id="admin", name="Library")
        note, (card,) = await notes.create_note(
            owner_id="admin",
            deck_id=deck.id,
            note_type=NoteType.BASIC,
            fields={"front": "Q", "back": "A"},
        )
    return note.id, card.id


class _DeletesFirst(SqlReviewLogRepository):
    """Commits the admin's deletion of the note from another session just after the
    member's review has started, before it writes anything."""

    def __init__(
        self, s: AsyncSession, sm: async_sessionmaker[AsyncSession], note_id: str
    ) -> None:
        super().__init__(s)
        self._sm, self._note_id = sm, note_id

    async def lock_owner(self, owner_id: str) -> None:
        async with self._sm() as other, other.begin():
            await _library(other).delete_note(owner_id="admin", note_id=self._note_id)
        await super().lock_owner(owner_id)


@pytest.mark.parametrize("key", [None, "offline-1"])
async def test_a_review_racing_the_cards_deletion_is_not_found(
    engine: AsyncEngine, key: str | None
) -> None:
    sm = create_sessionmaker(engine)
    note_id, card_id = await _seed(sm)

    with pytest.raises(CardNotFound):
        async with sm() as s, s.begin():
            await _reviews(s, _DeletesFirst(s, sm, note_id)).submit(
                owner_id="member", card_id=card_id, rating=Rating.GOOD, idempotency_key=key
            )

    async with sm() as s:
        assert await SqlReviewLogRepository(s).list_for_owner("member") == []
        assert await SqlScheduleRepository(s).list_for_user("member") == []


async def test_a_deletion_waits_for_a_review_in_progress_then_removes_it(
    engine: AsyncEngine,
) -> None:
    sm = create_sessionmaker(engine)
    note_id, card_id = await _seed(sm)

    async def delete_note() -> None:
        async with sm() as other, other.begin():
            await _library(other).delete_note(owner_id="admin", note_id=note_id)

    async with sm() as s, s.begin():
        await _reviews(s).submit(owner_id="member", card_id=card_id, rating=Rating.GOOD)
        deleting = asyncio.create_task(delete_note())
        await asyncio.sleep(0.3)
        assert not deleting.done()  # held off by the review's lock on the card row
    await deleting

    async with sm() as s:
        assert await SqlCardRepository(s).get(card_id) is None
        assert await SqlReviewLogRepository(s).list_for_owner("member") == []
        assert await SqlScheduleRepository(s).list_for_user("member") == []
