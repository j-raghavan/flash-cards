# SPDX-License-Identifier: AGPL-3.0-only
"""Integration tests for the AI SQLAlchemy adapters against a real Postgres.

These require Docker: a throwaway Postgres container is started per test session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from flashcards.adapters.db import AiJobRow, Base, create_engine, create_sessionmaker
from flashcards.adapters.sql import (
    SqlAiJobRepository,
    SqlDraftCardRepository,
    SqlObjectStore,
    SqlQuotaRepository,
)
from flashcards.domain.ai import DraftCardRecord, Thoroughness
from flashcards.domain.jobs import AiJob, JobStatus, new_generation_job
from flashcards.domain.notes import NoteType

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


def _job(owner: str = "alice") -> AiJob:
    return new_generation_job(
        owner_id=owner,
        source_id="src",
        draft_deck_id="deck",
        thoroughness=Thoroughness.STANDARD,
        now=NOW,
    )


def _draft(job_id: str, owner: str, record_id: str, page: int) -> DraftCardRecord:
    return DraftCardRecord(
        id=record_id,
        job_id=job_id,
        owner_id=owner,
        note_type=NoteType.BASIC,
        fields=(("front", f"Q{page}"), ("back", f"A{page}")),
        source_page=page,
        created_at=NOW,
    )


async def test_job_roundtrip_and_update(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    job = _job()
    async with sm() as s, s.begin():
        await SqlAiJobRepository(s).add(job)
    async with sm() as s, s.begin():
        repo = SqlAiJobRepository(s)
        loaded = await repo.get(job.id)
        assert loaded is not None and loaded.status is JobStatus.QUEUED
        await repo.update(loaded.started(NOW).with_total(3, NOW).advanced(NOW))
    async with sm() as s:
        loaded = await SqlAiJobRepository(s).get(job.id)
        assert loaded is not None
        assert loaded.status is JobStatus.RUNNING
        assert (loaded.total_units, loaded.completed_units) == (3, 1)
        assert await SqlAiJobRepository(s).get("missing") is None


async def test_try_claim_is_single_winner(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    job = _job()
    async with sm() as s, s.begin():
        await SqlAiJobRepository(s).add(job)
    async with sm() as s, s.begin():
        claimed = await SqlAiJobRepository(s).try_claim(job.id, NOW)
        assert claimed is not None and claimed.status is JobStatus.RUNNING
    # Already RUNNING: a second claim wins nothing.
    async with sm() as s, s.begin():
        assert await SqlAiJobRepository(s).try_claim(job.id, NOW) is None
        assert await SqlAiJobRepository(s).try_claim("missing", NOW) is None


async def test_request_cancel_and_list_running(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    job = _job()
    async with sm() as s, s.begin():
        await SqlAiJobRepository(s).add(job)
        await SqlAiJobRepository(s).try_claim(job.id, NOW)
    async with sm() as s, s.begin():
        repo = SqlAiJobRepository(s)
        assert await repo.request_cancel(job.id, NOW) is True
        # Already requested: no second change.
        assert await repo.request_cancel(job.id, NOW) is False
    async with sm() as s:
        running = await SqlAiJobRepository(s).list_running()
        assert [j.id for j in running] == [job.id]
        assert running[0].cancel_requested is True


async def test_draft_crud_ordering_and_cascade(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    job = _job()
    async with sm() as s, s.begin():
        await SqlAiJobRepository(s).add(job)
        drafts = SqlDraftCardRepository(s)
        await drafts.add(_draft(job.id, "alice", "d2", page=2))
        await drafts.add(_draft(job.id, "alice", "d1", page=1))
    async with sm() as s, s.begin():
        drafts = SqlDraftCardRepository(s)
        listed = await drafts.list_for_job(job.id)
        assert [d.source_page for d in listed] == [1, 2]  # tie-broken by page
        assert (await drafts.get("d1")) is not None
        await drafts.delete("d1")
        assert (await drafts.get("d1")) is None
        await drafts.delete("missing")  # deleting an absent draft is a no-op
        await drafts.delete_for_job(job.id)  # clears the rest (d2)
        assert await drafts.list_for_job(job.id) == []
    # Deleting the job cascades to any drafts (re-add one first).
    async with sm() as s, s.begin():
        await SqlDraftCardRepository(s).add(_draft(job.id, "alice", "d3", page=3))
    async with sm() as s, s.begin():
        row = await s.get(AiJobRow, job.id)
        assert row is not None
        await s.delete(row)
    async with sm() as s:
        assert await SqlDraftCardRepository(s).list_for_job(job.id) == []


async def test_object_store_put_get_overwrite_delete(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlObjectStore(s).put("k1", b"hello", "application/pdf")
    async with sm() as s, s.begin():
        store = SqlObjectStore(s)
        assert await store.get("k1") == b"hello"
        await store.put("k1", b"world", "application/pdf")  # overwrite
    async with sm() as s, s.begin():
        store = SqlObjectStore(s)
        assert await store.get("k1") == b"world"
        assert await store.get("missing") is None
        await store.delete("k1")
        assert await store.get("k1") is None
        await store.delete("missing")  # deleting an absent blob is a no-op


async def test_quota_reserves_atomically_up_to_the_limit(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)

    async def reserve(owner: str, pages: int, period: str = "2026-11") -> bool:
        async with sm() as s, s.begin():
            return await SqlQuotaRepository(s, limit=10).try_reserve_pages(owner, pages, period)

    assert await reserve("alice", 4) is True
    assert await reserve("alice", 5) is True  # used 9
    assert await reserve("alice", 2) is False  # 9 + 2 > 10, rejected
    assert await reserve("alice", 1) is True  # used 10 exactly
    assert await reserve("alice", 1) is False  # full
    # A single request larger than the whole limit is refused outright.
    assert await reserve("bob", 11) is False
    # A different period is counted separately.
    assert await reserve("alice", 10, period="2026-12") is True


def test_sync_sessionmaker_type(engine: AsyncEngine) -> None:
    # create_sessionmaker returns an async_sessionmaker (guards the import wiring).
    assert isinstance(create_sessionmaker(engine), async_sessionmaker)
