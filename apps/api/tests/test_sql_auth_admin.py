# SPDX-License-Identifier: AGPL-3.0-only
"""Postgres tests for session activity (needs Docker)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from flashcards.adapters.db import (
    Base,
    SessionActivityRow,
    create_engine,
    create_sessionmaker,
)
from flashcards.adapters.sql import (
    SqlSessionRepository,
)
from flashcards.domain.sessions import Session

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


def _session(sid: str, user_id: str = "u1", created_at: datetime = NOW) -> Session:
    return Session(
        id=sid,
        user_id=user_id,
        token_hash=f"hash-{sid}",
        created_at=created_at,
        expires_at=created_at + timedelta(days=14),
        last_seen_at=created_at,
        device="Firefox on macOS",
    )


async def test_session_activity_device_and_revoking_others(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        repo = SqlSessionRepository(s)
        await repo.add(_session("s1"))
        await repo.add(_session("s2", created_at=NOW + timedelta(minutes=1)))
        await repo.add(_session("s3", created_at=NOW + timedelta(minutes=2)))
        await repo.add(_session("other", user_id="u2"))
        # A session stored without a last-seen time starts at its creation.
        await repo.add(
            Session(id="bare", user_id="u3", token_hash="hash-bare", created_at=NOW,
                    expires_at=NOW + timedelta(days=1))
        )

    async with sm() as s, s.begin():
        repo = SqlSessionRepository(s)
        found = await repo.get_by_token_hash("hash-s1")
        assert found is not None
        assert (found.device, found.last_seen_at) == ("Firefox on macOS", NOW)
        bare = await repo.get_by_token_hash("hash-bare")
        assert bare is not None and bare.last_seen_at == NOW and bare.device == "Unknown device"
        assert await repo.get_by_token_hash("missing") is None

        later = NOW + timedelta(hours=1)
        await repo.touch("s1", later)
        await repo.touch("missing", later)  # nothing to update, no error

    async with sm() as s, s.begin():
        repo = SqlSessionRepository(s)
        listed = await repo.list_for_user("u1")
        assert [x.id for x in listed] == ["s3", "s2", "s1"]
        assert listed[2].last_seen_at == NOW + timedelta(hours=1)
        assert await repo.revoke_others("u1", "s3", NOW) == 2
        assert await repo.revoke_others("u1", "s3", NOW) == 0

    async with sm() as s, s.begin():
        repo = SqlSessionRepository(s)
        states = {x.id: x.revoked_at for x in await repo.list_for_user("u1")}
        assert states["s3"] is None and states["s1"] == NOW and states["s2"] == NOW
        other = await repo.get_by_token_hash("hash-other")
        assert other is not None and other.revoked_at is None
        # Deleting a user's sessions takes their activity rows with them.
        await repo.delete_for_user("u1")

    async with sm() as s:
        remaining = await s.scalars(select(SessionActivityRow.session_id))
        assert sorted(remaining) == ["bare", "other"]


async def test_touch_skips_a_row_another_transaction_is_writing(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlSessionRepository(s).add(_session("s1"))

    async with sm() as first, first.begin():
        await SqlSessionRepository(first).touch("s1", NOW + timedelta(minutes=10))
        # The first transaction still holds the row: the second skips, never waits.
        async with sm() as second, second.begin():
            await SqlSessionRepository(second).touch("s1", NOW + timedelta(minutes=20))

    async with sm() as s:
        found = await SqlSessionRepository(s).get_by_token_hash("hash-s1")
        assert found is not None and found.last_seen_at == NOW + timedelta(minutes=10)
