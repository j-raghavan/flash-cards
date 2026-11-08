# SPDX-License-Identifier: AGPL-3.0-only
"""Integration tests for the export read-paths against a real Postgres (needs Docker)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from flashcards.adapters.db import Base, create_engine, create_sessionmaker
from flashcards.adapters.sql import (
    SqlAiJobRepository,
    SqlCardRepository,
    SqlDeckRepository,
    SqlNoteRepository,
    SqlReviewLogRepository,
    SqlSessionRepository,
    SqlUserRepository,
)
from flashcards.domain.ai import Thoroughness
from flashcards.domain.decks import Deck
from flashcards.domain.invites import Role
from flashcards.domain.jobs import new_generation_job
from flashcards.domain.notes import Card, Note, NoteType
from flashcards.domain.scheduling import Rating, ReviewLog
from flashcards.domain.sessions import Session
from flashcards.domain.users import User

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


async def test_user_session_and_aijob_deletion(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlUserRepository(s).add(
            User(id="u1", email="u@e.com", password_hash="h", role=Role.MEMBER, created_at=NOW)
        )
        sessions = SqlSessionRepository(s)
        for sid, token in (("s1", "t1"), ("s2", "t2")):
            await sessions.add(
                Session(
                    id=sid, user_id="u1", token_hash=token, created_at=NOW,
                    expires_at=NOW + timedelta(days=1), revoked_at=None,
                )
            )
        await SqlAiJobRepository(s).add(
            new_generation_job(
                owner_id="u1", source_id="src", draft_deck_id="d",
                thoroughness=Thoroughness.STANDARD, now=NOW,
            )
        )

    async with sm() as s:
        assert len(await SqlAiJobRepository(s).list_for_owner("u1")) == 1

    async with sm() as s, s.begin():
        await SqlSessionRepository(s).delete_for_user("u1")
        jobs = SqlAiJobRepository(s)
        for job in await jobs.list_for_owner("u1"):
            await jobs.delete(job.id)
        await SqlUserRepository(s).delete("u1")

    async with sm() as s:
        assert await SqlUserRepository(s).get("u1") is None
        assert await SqlSessionRepository(s).list_for_user("u1") == []
        assert await SqlAiJobRepository(s).list_for_owner("u1") == []


async def test_mock_exam_roundtrip_and_owner_delete(engine: AsyncEngine) -> None:
    from flashcards.adapters.sql import SqlMockExamRepository
    from flashcards.domain.mock_exam import ExamQuestion, MockExam

    sm = create_sessionmaker(engine)
    exam = MockExam(
        id="e1", owner_id="alice", deck_id="d1", created_at=NOW, time_limit_seconds=600,
        questions=(
            ExamQuestion(id="q1", prompt="p1", reference="r1", topic="Bio"),
            ExamQuestion(id="q2", prompt="p2", reference="r2", topic="Chem"),
        ),
    )
    async with sm() as s, s.begin():
        await SqlMockExamRepository(s).add(exam)
        await SqlMockExamRepository(s).add(
            MockExam(
                id="e2", owner_id="bob", deck_id="d9", created_at=NOW, time_limit_seconds=None,
                questions=(ExamQuestion(id="q", prompt="p", reference="r", topic="t"),),
            )
        )

    async with sm() as s:
        loaded = await SqlMockExamRepository(s).get("e1")
        assert loaded is not None
        assert loaded.time_limit_seconds == 600
        assert [q.reference for q in loaded.questions] == ["r1", "r2"]
        assert await SqlMockExamRepository(s).get("missing") is None

    async with sm() as s, s.begin():
        await SqlMockExamRepository(s).delete_for_owner("alice")

    async with sm() as s:
        assert await SqlMockExamRepository(s).get("e1") is None
        assert await SqlMockExamRepository(s).get("e2") is not None  # Bob's exam survives


async def test_media_roundtrip(engine: AsyncEngine) -> None:
    from flashcards.adapters.sql import SqlMediaRepository
    from flashcards.domain.media import MediaAsset

    sm = create_sessionmaker(engine)
    asset = MediaAsset(
        id="m1", owner_id="alice", content_type="image/png", data=b"\x89PNG-bytes",
        created_at=NOW,
    )
    async with sm() as s, s.begin():
        await SqlMediaRepository(s).add(asset)
    async with sm() as s:
        loaded = await SqlMediaRepository(s).get("m1")
        assert loaded is not None
        assert loaded.content_type == "image/png" and loaded.data == b"\x89PNG-bytes"
        assert await SqlMediaRepository(s).get("missing") is None


async def test_owner_scoped_deletes(engine: AsyncEngine) -> None:
    from flashcards.adapters.sql import (
        SqlMediaRepository,
        SqlQuotaRepository,
        SqlReviewLogRepository,
    )
    from flashcards.domain.media import MediaAsset

    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        media = SqlMediaRepository(s)
        await media.add(
            MediaAsset(
                id="m1", owner_id="alice", content_type="image/png", data=b"x", created_at=NOW
            )
        )
        await media.add(
            MediaAsset(
                id="m2", owner_id="bob", content_type="image/png", data=b"y", created_at=NOW
            )
        )
        # A deck+note+card so the review log's FK is satisfied, then an owner-scoped log.
        await SqlDeckRepository(s).add(
            Deck(id="dk", owner_id="alice", name="D", parent_id=None, created_at=NOW)
        )
        await SqlNoteRepository(s).add(
            Note(
                id="n", owner_id="alice", deck_id="dk", note_type=NoteType.BASIC,
                fields=(("front", "q"), ("back", "a")), created_at=NOW,
            )
        )
        await SqlCardRepository(s).add(
            Card(
                id="ck", owner_id="alice", deck_id="dk", note_id="n", ordinal=0,
                created_at=NOW, suspended_at=None,
            )
        )
        await SqlReviewLogRepository(s).append(
            ReviewLog(id="rl", owner_id="alice", card_id="ck", rating=Rating.GOOD, reviewed_at=NOW)
        )
        assert await SqlQuotaRepository(s).try_reserve_pages("alice", 2, "2026-11") is True

    async with sm() as s:
        assert [m.id for m in await SqlMediaRepository(s).list_for_owner("alice")] == ["m1"]

    async with sm() as s, s.begin():
        await SqlMediaRepository(s).delete_for_owner("alice")
        await SqlReviewLogRepository(s).delete_for_owner("alice")
        await SqlQuotaRepository(s).delete_for_owner("alice")

    async with sm() as s:
        assert await SqlMediaRepository(s).list_for_owner("alice") == []
        assert await SqlReviewLogRepository(s).list_for_owner("alice") == []
        # Alice's quota counter is gone, so a fresh reservation succeeds.
        assert await SqlQuotaRepository(s).try_reserve_pages("alice", 1, "2026-11") is True
        # Bob's media untouched.
        assert [m.id for m in await SqlMediaRepository(s).list_for_owner("bob")] == ["m2"]
