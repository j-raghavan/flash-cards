# SPDX-License-Identifier: AGPL-3.0-only
"""Undo of the last answer against a real Postgres (needs Docker)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from flashcards.adapters.db import Base, create_engine, create_sessionmaker
from flashcards.adapters.fsrs_scheduler import FsrsScheduler
from flashcards.adapters.sql import (
    SqlCardRepository,
    SqlDeckRepository,
    SqlMediaRepository,
    SqlNoteRepository,
    SqlNoteTypeRepository,
    SqlReviewLogRepository,
    SqlScheduleRepository,
    SqlUserRepository,
)
from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.application.read_access import ReadAccess
from flashcards.application.review_service import ReviewService
from flashcards.domain.decks import Deck
from flashcards.domain.notes import Card, Note, NoteType
from flashcards.domain.scheduling import (
    CardSchedule,
    LastAnswer,
    Rating,
    ReviewLog,
    ReviewNotFound,
    ScheduleState,
    UndoSnapshot,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


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


def _schedule(card_id: str, due_at: datetime) -> CardSchedule:
    return CardSchedule(
        card_id=card_id,
        state=ScheduleState.REVIEW,
        step=None,
        stability=4.0,
        difficulty=5.0,
        due_at=due_at,
        last_reviewed_at=due_at - timedelta(days=4),
    )


async def _seed(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlDeckRepository(s).add(
            Deck(id="dA", owner_id="alice", name="A", parent_id=None, created_at=NOW)
        )
        await SqlNoteRepository(s).add(
            Note(
                id="na1",
                owner_id="alice",
                deck_id="dA",
                note_type=NoteType.BASIC,
                fields=(("front", "Q"), ("back", "A")),
                created_at=NOW,
            )
        )
        await SqlCardRepository(s).add(
            Card(id="a1", owner_id="alice", deck_id="dA", note_id="na1", ordinal=0, created_at=NOW)
        )


async def test_review_log_lookup_latest_and_delete(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    await _seed(engine)
    prior = _schedule("a1", NOW)
    async with sm() as s, s.begin():
        repo = SqlReviewLogRepository(s)
        await repo.lock_owner("alice")
        await repo.append(
            ReviewLog(
                id="keyed",
                owner_id="alice",
                card_id="a1",
                rating=Rating.GOOD,
                reviewed_at=NOW,
                idempotency_key="k",
                undo=UndoSnapshot(prior),
            )
        )
    async with sm() as s, s.begin():
        repo = SqlReviewLogRepository(s)
        found = await repo.find_by_key("alice", "a1", "k")
        assert found is not None and found.undo == UndoSnapshot(prior)
        assert await repo.find_by_key("bob", "a1", "k") is None
        assert await repo.last_answer("alice") is None
        await repo.set_last_answer(found)
        assert await repo.last_answer("alice") == LastAnswer("alice", "keyed", "a1", "k")
        await repo.mark_undone("alice")
        last = await repo.last_answer("alice")
        assert last is not None and last.undone is True
        await repo.set_last_answer(found)  # a new answer resets the flag
        last = await repo.last_answer("alice")
        assert last is not None and last.undone is False
        assert await repo.last_answer("nobody") is None
        assert await repo.get("missing") is None
        assert await repo.try_delete("keyed") is True
        assert await repo.try_delete("keyed") is False
        assert await repo.get("keyed") is None


async def test_answer_and_undo_through_sql(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    clock = FakeClock()
    async with sm() as s, s.begin():
        decks = DeckService(SqlDeckRepository(s), clock)
        notes = NoteService(decks, SqlNoteRepository(s), SqlCardRepository(s), clock)
        deck = await decks.create(owner_id="alice", name="Bio")
        _note_row, cards = await notes.create_note(
            owner_id="alice",
            deck_id=deck.id,
            note_type=NoteType.BASIC,
            fields={"front": "Q", "back": "A"},
        )
    card_id = cards[0].id

    def reviews(s: AsyncSession) -> ReviewService:
        access = ReadAccess(
            SqlUserRepository(s),
            SqlDeckRepository(s),
            SqlNoteRepository(s),
            SqlCardRepository(s),
            SqlNoteTypeRepository(s),
            SqlMediaRepository(s),
        )
        return ReviewService(
            access, SqlScheduleRepository(s), SqlReviewLogRepository(s), FsrsScheduler(), clock
        )

    async with sm() as s, s.begin():
        outcome = await reviews(s).submit(owner_id="alice", card_id=card_id, rating=Rating.GOOD)
    async with sm() as s, s.begin():
        undone = await reviews(s).undo(owner_id="alice", review_id=outcome.review_id)
        assert undone.schedule is None
    async with sm() as s, s.begin():
        assert await SqlScheduleRepository(s).get("alice", card_id) is None
        with pytest.raises(ReviewNotFound):
            await reviews(s).undo(owner_id="alice", review_id=outcome.review_id)


async def test_account_deletion_clears_the_last_answer(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    await _seed(engine)
    log = ReviewLog(id="r1", owner_id="alice", card_id="a1", rating=Rating.GOOD, reviewed_at=NOW)
    async with sm() as s, s.begin():
        repo = SqlReviewLogRepository(s)
        await repo.append(log)
        await repo.set_last_answer(log)
    async with sm() as s, s.begin():
        await SqlReviewLogRepository(s).delete_for_owner("alice")
    async with sm() as s:
        assert await SqlReviewLogRepository(s).last_answer("alice") is None
