# SPDX-License-Identifier: AGPL-3.0-only
"""Integration tests for the export read-paths against a real Postgres (needs Docker)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from flashcards.adapters.db import Base, create_engine, create_sessionmaker
from flashcards.adapters.sql import (
    SqlCardRepository,
    SqlDeckRepository,
    SqlNoteRepository,
    SqlReviewLogRepository,
)
from flashcards.domain.decks import Deck
from flashcards.domain.notes import Card, Note, NoteType
from flashcards.domain.scheduling import Rating, ReviewLog

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def test_note_list_for_deck_and_review_log_list_for_owner(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlDeckRepository(s).add(
            Deck(id="d1", owner_id="alice", name="Bio", parent_id=None, created_at=NOW)
        )
        notes = SqlNoteRepository(s)
        await notes.add(
            Note(
                id="n1", owner_id="alice", deck_id="d1", note_type=NoteType.BASIC,
                fields=(("front", "q"), ("back", "a")), created_at=NOW,
            )
        )
        await SqlCardRepository(s).add(
            Card(
                id="c1", owner_id="alice", deck_id="d1", note_id="n1", ordinal=0,
                created_at=NOW, suspended_at=None,
            )
        )
        logs = SqlReviewLogRepository(s)
        await logs.append(
            ReviewLog(
                id="r2", owner_id="alice", card_id="c1",
                rating=Rating.GOOD, reviewed_at=NOW + timedelta(hours=1),
            )
        )
        await logs.append(
            ReviewLog(
                id="r1", owner_id="alice", card_id="c1", rating=Rating.AGAIN, reviewed_at=NOW
            )
        )
        await logs.append(
            ReviewLog(
                id="rb", owner_id="bob", card_id="c1", rating=Rating.EASY, reviewed_at=NOW
            )
        )

    async with sm() as s:
        assert [n.id for n in await SqlNoteRepository(s).list_for_deck("d1")] == ["n1"]
        assert await SqlNoteRepository(s).list_for_deck("missing") == []
        # Oldest first, and scoped to the owner.
        owner_logs = await SqlReviewLogRepository(s).list_for_owner("alice")
        assert [log.id for log in owner_logs] == ["r1", "r2"]
        assert owner_logs[0].rating is Rating.AGAIN
