# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import InMemorySessionRepository, InMemoryUserRepository
from flashcards.application.auth_service import (
    AccountSuspended,
    AuthService,
    InvalidCredentials,
)
from flashcards.domain.invites import Role, hash_token
from flashcards.domain.users import User

START = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
PASSWORD = "correct-horse-battery"
EMAIL = "person@example.com"


class FakeClock:
    def __init__(self, now: datetime = START) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class FakeHasher:
    def hash(self, password: str) -> str:
        return f"hashed:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == f"hashed:{password}"


def make_user(suspended: bool = False) -> User:
    user = User(
        id="user-1",
        email=EMAIL,
        password_hash=f"hashed:{PASSWORD}",
        role=Role.MEMBER,
        created_at=START,
    )
    return user.with_suspended(START) if suspended else user


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def users() -> InMemoryUserRepository:
    return InMemoryUserRepository()


@pytest.fixture
def sessions() -> InMemorySessionRepository:
    return InMemorySessionRepository()


@pytest.fixture
def service(
    users: InMemoryUserRepository, sessions: InMemorySessionRepository, clock: FakeClock
) -> AuthService:
    return AuthService(users, sessions, FakeHasher(), clock)


async def test_login_issues_a_session_for_valid_credentials(
    service: AuthService, users: InMemoryUserRepository, sessions: InMemorySessionRepository
) -> None:
    await users.add(make_user())
    issued = await service.login(email="  Person@Example.COM ", password=PASSWORD)
    assert issued.session.user_id == "user-1"
    assert issued.session.expires_at == START + timedelta(days=14)
    stored = await sessions.get_by_token_hash(hash_token(issued.raw_token))
    assert stored is not None
    assert stored.id == issued.session.id


async def test_login_unknown_email_is_invalid(service: AuthService) -> None:
    with pytest.raises(InvalidCredentials):
        await service.login(email="nobody@example.com", password=PASSWORD)


async def test_login_wrong_password_is_invalid(
    service: AuthService, users: InMemoryUserRepository
) -> None:
    await users.add(make_user())
    with pytest.raises(InvalidCredentials):
        await service.login(email=EMAIL, password="wrong-password-xyz")


async def test_login_suspended_account_is_rejected(
    service: AuthService, users: InMemoryUserRepository
) -> None:
    await users.add(make_user(suspended=True))
    with pytest.raises(AccountSuspended):
        await service.login(email=EMAIL, password=PASSWORD)


async def test_authenticate_returns_the_live_session(
    service: AuthService, users: InMemoryUserRepository
) -> None:
    await users.add(make_user())
    issued = await service.login(email=EMAIL, password=PASSWORD)
    found = await service.authenticate(issued.raw_token)
    assert found is not None
    assert found.id == issued.session.id


async def test_authenticate_unknown_token_is_none(service: AuthService) -> None:
    assert await service.authenticate("not-a-session-token") is None


async def test_authenticate_expired_session_is_none(
    service: AuthService, users: InMemoryUserRepository, clock: FakeClock
) -> None:
    await users.add(make_user())
    issued = await service.login(email=EMAIL, password=PASSWORD)
    clock.advance(timedelta(days=14))
    assert await service.authenticate(issued.raw_token) is None


async def test_logout_revokes_the_session(
    service: AuthService, users: InMemoryUserRepository
) -> None:
    await users.add(make_user())
    issued = await service.login(email=EMAIL, password=PASSWORD)
    assert await service.logout(issued.raw_token) is True
    assert await service.authenticate(issued.raw_token) is None


async def test_logout_unknown_token_is_false(service: AuthService) -> None:
    assert await service.logout("not-a-session-token") is False


async def test_logout_is_idempotent_only_once(
    service: AuthService, users: InMemoryUserRepository
) -> None:
    await users.add(make_user())
    issued = await service.login(email=EMAIL, password=PASSWORD)
    assert await service.logout(issued.raw_token) is True
    assert await service.logout(issued.raw_token) is False


async def test_sessions_for_lists_newest_first(
    service: AuthService, users: InMemoryUserRepository, clock: FakeClock
) -> None:
    await users.add(make_user())
    first = await service.login(email=EMAIL, password=PASSWORD)
    clock.advance(timedelta(minutes=5))
    second = await service.login(email=EMAIL, password=PASSWORD)
    listed = await service.sessions_for("user-1")
    assert [s.id for s in listed] == [second.session.id, first.session.id]


async def test_revoke_session_by_id(
    service: AuthService, users: InMemoryUserRepository
) -> None:
    await users.add(make_user())
    issued = await service.login(email=EMAIL, password=PASSWORD)
    assert await service.revoke_session(issued.session.id) is True
    assert await service.revoke_session(issued.session.id) is False
