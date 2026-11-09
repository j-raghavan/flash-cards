# SPDX-License-Identifier: AGPL-3.0-only
"""Postgres tests for the security follow-ups of ADR 0019 (needs Docker)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flashcards.adapters.db import Base, create_engine, create_sessionmaker
from flashcards.adapters.sql import (
    SqlAuditRepository,
    SqlLoginThrottleRepository,
    SqlSpendHoldRepository,
    SqlUsageRepository,
    SqlUserRepository,
)
from flashcards.application.audit_service import AuditService
from flashcards.application.public_signup_service import (
    PublicSignupService,
    signup_throttle_key,
)
from flashcards.domain.invites import Role
from flashcards.domain.usage import AiFeature, AiUsage, SpendHold
from flashcards.domain.users import EmailAlreadyRegistered, User

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
EMAIL = "race@example.com"


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Hasher:
    async def hash(self, password: str) -> str:
        return f"hashed:{password}"

    async def verify(self, password_hash: str, password: str) -> bool:  # pragma: no cover
        return False


def _user(user_id: str) -> User:
    return User(id=user_id, email=EMAIL, password_hash="h", role=Role.MEMBER, created_at=NOW)


class _RacingUsers(SqlUserRepository):
    """Another sign-up commits the same email between this one's lookup and insert."""

    def __init__(self, session: AsyncSession, sm: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(session)
        self._sm = sm
        self._raced = False

    async def get_by_email(self, email: str) -> User | None:
        found = await super().get_by_email(email)
        if not self._raced:
            self._raced = True
            async with self._sm() as other, other.begin():
                await SqlUserRepository(other).add(_user("winner"))
        return found


async def test_a_duplicate_insert_leaves_the_transaction_usable(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlUserRepository(s).add(_user("u1"))
    async with sm() as s, s.begin():
        repo = SqlUserRepository(s)
        with pytest.raises(EmailAlreadyRegistered):
            await repo.add(_user("u2"))
        # Only the insert was undone: the transaction goes on and commits.
        await SqlLoginThrottleRepository(s).register_attempt("key", NOW)
    async with sm() as s:
        assert (await SqlLoginThrottleRepository(s).get("key")) is not None
        assert await SqlUserRepository(s).get("u2") is None


async def test_concurrent_public_sign_ups_answer_as_for_an_existing_account(
    engine: AsyncEngine,
) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        users = _RacingUsers(s, sm)
        service = PublicSignupService(
            users,
            _Hasher(),
            SqlLoginThrottleRepository(s),
            AuditService(SqlAuditRepository(s), _Clock()),
            _Clock(),
        )
        outcome = await service.register(email=EMAIL, password="x" * 16, client="1.2.3.4")
    assert outcome.created is False and outcome.user.id == "winner"
    async with sm() as s:
        # The losing request committed: its throttle attempt counts, and no
        # audit event or second account was written.
        state = await SqlLoginThrottleRepository(s).get(signup_throttle_key("1.2.3.4"))
        assert state is not None and state.failures == 1
        assert await SqlAuditRepository(s).list_recent(limit=5) == []


def _hold(hold_id: str, amount: int, expires_at: datetime = NOW + timedelta(hours=1)) -> SpendHold:
    return SpendHold(id=hold_id, period="2026-11", amount_micro_usd=amount, expires_at=expires_at)


async def test_concurrent_holds_never_jointly_pass_the_cap(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlUsageRepository(s).add(
            AiUsage(id="u", owner_id="o", provider="p", model="m", feature=AiFeature.CHAT,
                    input_tokens=1, output_tokens=1, cost_micro_usd=100, period="2026-11",
                    created_at=NOW)
        )

    async def attempt(index: int) -> bool:
        async with sm() as s:
            repo = SqlSpendHoldRepository(s, sm)
            return await repo.try_hold(_hold(f"h{index}", 100), cap_micro_usd=1000, now=NOW)

    # Twenty calls race for room: 100 spent, then holds of 100 while under 1000.
    results = await asyncio.gather(*(attempt(i) for i in range(20)))
    assert results.count(True) == 9
    async with sm() as s:
        repo = SqlSpendHoldRepository(s, sm)
        assert await repo.held("2026-11", NOW) == 900


async def test_a_hold_settles_with_the_usage_and_releases_or_lapses(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s:
        repo = SqlSpendHoldRepository(s, sm)
        assert await repo.try_hold(_hold("a", 300), cap_micro_usd=500, now=NOW)
        assert await repo.try_hold(_hold("b", 300, NOW + timedelta(minutes=1)),
                                   cap_micro_usd=500, now=NOW)
        assert not await repo.try_hold(_hold("c", 1), cap_micro_usd=500, now=NOW)
        await repo.release("a")
    async with sm() as s, s.begin():
        # Settling happens inside the caller's transaction, with the usage record.
        repo = SqlSpendHoldRepository(s, sm)
        assert await repo.held("2026-11", NOW) == 300
        await repo.settle("b")
        assert await repo.held("2026-11", NOW) == 0
    async with sm() as s:
        repo = SqlSpendHoldRepository(s, sm)
        assert await repo.held("2026-11", NOW) == 0
        # An expired hold no longer counts, and the next hold purges it.
        assert await repo.try_hold(_hold("d", 400, NOW + timedelta(minutes=1)),
                                   cap_micro_usd=500, now=NOW)
        later = NOW + timedelta(minutes=2)
        assert await repo.held("2026-11", later) == 0
        assert await repo.try_hold(_hold("e", 1), cap_micro_usd=500, now=later)
        assert await repo.held("2026-11", NOW) == 1  # "d" is gone, not just expired
