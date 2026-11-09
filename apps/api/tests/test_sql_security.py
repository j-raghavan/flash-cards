# SPDX-License-Identifier: AGPL-3.0-only
"""Postgres tests for the security follow-ups of ADR 0019 (needs Docker)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flashcards.adapters.db import Base, create_engine, create_sessionmaker
from flashcards.adapters.sql import (
    SqlAuditRepository,
    SqlLoginThrottleRepository,
    SqlUserRepository,
)
from flashcards.application.audit_service import AuditService
from flashcards.application.public_signup_service import (
    PublicSignupService,
    signup_throttle_key,
)
from flashcards.domain.invites import Role
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
