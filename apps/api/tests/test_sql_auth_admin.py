# SPDX-License-Identifier: AGPL-3.0-only
"""Postgres tests for session activity, invite rotation, quotas and usage (needs Docker)."""

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
    SqlInviteRepository,
    SqlMockExamRepository,
    SqlQuotaRepository,
    SqlQuotaSettingsRepository,
    SqlSessionRepository,
    SqlUsageRepository,
    SqlUserRepository,
)
from flashcards.domain.invites import Invite, Role
from flashcards.domain.mock_exam import ExamQuestion, MockExam
from flashcards.domain.quotas import InstanceQuotas
from flashcards.domain.sessions import Session
from flashcards.domain.usage import AiFeature, AiUsage, ProviderUsage, UserUsage
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


async def test_only_an_active_invite_gets_a_new_token(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)

    def invite(iid: str, **extra: datetime) -> Invite:
        return Invite(id=iid, token_hash=f"old-{iid}", role=Role.MEMBER, created_by="admin",
                      created_at=NOW, expires_at=NOW + timedelta(days=7),
                      email=f"{iid}@example.com", **extra)

    async with sm() as s, s.begin():
        repo = SqlInviteRepository(s)
        await repo.add(invite("active"))
        await repo.add(invite("revoked", revoked_at=NOW))
        await repo.add(invite("redeemed", redeemed_at=NOW))

    async with sm() as s, s.begin():
        repo = SqlInviteRepository(s)
        assert await repo.try_rotate_token("active", "new-active", NOW) is True
        assert await repo.try_rotate_token("revoked", "new-revoked", NOW) is False
        assert await repo.try_rotate_token("redeemed", "new-redeemed", NOW) is False
        assert await repo.try_rotate_token("missing", "new-missing", NOW) is False
        # At or past the expiry the invite is no longer active.
        assert await repo.try_rotate_token("active", "late", NOW + timedelta(days=7)) is False

    async with sm() as s:
        repo = SqlInviteRepository(s)
        assert await repo.get_by_token_hash("old-active") is None
        rotated = await repo.get_by_token_hash("new-active")
        assert rotated is not None and rotated.id == "active"


async def test_page_reservations_honour_a_per_call_limit(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        pages = SqlQuotaRepository(s, limit=10)
        assert await pages.used("u1", "2026-11") == 0
        assert await pages.try_reserve_pages("u1", 3, "2026-11", limit=4) is True
        assert await pages.try_reserve_pages("u1", 2, "2026-11", limit=4) is False
        assert await pages.try_reserve_pages("u1", 5, "2026-11", limit=4) is False
        assert await pages.try_reserve_pages("u1", 2, "2026-11") is True  # repository limit
        assert await pages.used("u1", "2026-11") == 5


async def test_quota_settings_round_trip_and_follow_the_user(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlUserRepository(s).add(
            User(id="u1", email="u@example.com", password_hash="h", role=Role.MEMBER,
                 created_at=NOW)
        )
        settings = SqlQuotaSettingsRepository(s)
        assert await settings.get_instance() is None
        await settings.put_instance(InstanceQuotas(default_monthly_pages=5))
        await settings.put_instance(
            InstanceQuotas(default_monthly_pages=7, monthly_spend_cap_micro_usd=1_000_000)
        )
        assert await settings.get_user_pages("u1") is None
        await settings.set_user_pages("u1", 3)
        await settings.set_user_pages("u1", 4)

    async with sm() as s, s.begin():
        settings = SqlQuotaSettingsRepository(s)
        assert await settings.get_instance() == InstanceQuotas(
            default_monthly_pages=7, monthly_spend_cap_micro_usd=1_000_000
        )
        assert await settings.get_user_pages("u1") == 4
        await settings.set_user_pages("u1", None)
        assert await settings.get_user_pages("u1") is None
        await settings.set_user_pages("u1", 9)
        await SqlUserRepository(s).delete("u1")

    async with sm() as s:
        assert await SqlQuotaSettingsRepository(s).get_user_pages("u1") is None


def _usage(uid: str, owner: str, model: str, cost: int, period: str = "2026-11") -> AiUsage:
    return AiUsage(id=uid, owner_id=owner, provider="anthropic", model=model,
                   feature=AiFeature.GRADING, input_tokens=100, output_tokens=10,
                   cost_micro_usd=cost, period=period, created_at=NOW)


async def test_usage_totals_by_user_and_provider(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        usage = SqlUsageRepository(s)
        assert await usage.total_cost("2026-11") == 0
        await usage.add(_usage("a", "u1", "m1", 100))
        await usage.add(_usage("b", "u2", "m2", 300))
        await usage.add(_usage("c", "u2", "m1", 50))
        await usage.add(_usage("d", "u1", "m1", 999, period="2026-10"))

    async with sm() as s:
        usage = SqlUsageRepository(s)
        assert await usage.total_cost("2026-11") == 450
        assert await usage.by_user("2026-11") == [
            UserUsage("u2", 2, 200, 20, 350),
            UserUsage("u1", 1, 100, 10, 100),
        ]
        assert await usage.by_provider("2026-11") == [
            ProviderUsage("anthropic", "m2", 1, 100, 10, 300),
            ProviderUsage("anthropic", "m1", 2, 200, 20, 150),
        ]
        assert await usage.by_user("2026-09") == []


async def test_a_reopened_exam_can_be_submitted_again(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    exam = MockExam(
        id="e1", owner_id="alice", deck_id="d1", created_at=NOW, time_limit_seconds=None,
        questions=(ExamQuestion(id="q1", prompt="p", reference="r", topic="t"),),
    )
    async with sm() as s, s.begin():
        exams = SqlMockExamRepository(s)
        await exams.add(exam)
        assert await exams.try_mark_submitted("e1", NOW) is True
        await exams.reopen("e1")
        await exams.reopen("missing")  # nothing to reopen, no error

    async with sm() as s, s.begin():
        exams = SqlMockExamRepository(s)
        loaded = await exams.get("e1")
        assert loaded is not None and loaded.submitted_at is None
        assert await exams.try_mark_submitted("e1", NOW) is True
