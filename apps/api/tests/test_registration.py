# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import InMemoryInviteRepository, InMemoryUserRepository
from flashcards.application.invite_service import InviteService
from flashcards.application.registration_service import RegistrationService
from flashcards.domain.invites import InviteExpired, InviteNotFound, InviteStatus, Role
from flashcards.domain.users import (
    EmailAlreadyRegistered,
    InvalidEmail,
    User,
    WeakPassword,
)

START = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
PASSWORD = "correct-horse-battery"


class FakeClock:
    def __init__(self, now: datetime = START) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class FakeHasher:
    """Deterministic stand-in so service tests stay fast and assertable."""

    def hash(self, password: str) -> str:
        return f"hashed:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == f"hashed:{password}"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def invite_repo() -> InMemoryInviteRepository:
    return InMemoryInviteRepository()


@pytest.fixture
def invites(invite_repo: InMemoryInviteRepository, clock: FakeClock) -> InviteService:
    return InviteService(invite_repo, clock)


@pytest.fixture
def users() -> InMemoryUserRepository:
    return InMemoryUserRepository()


@pytest.fixture
def service(
    invites: InviteService, users: InMemoryUserRepository, clock: FakeClock
) -> RegistrationService:
    return RegistrationService(invites, users, FakeHasher(), clock)


async def test_register_creates_account_from_invite(
    service: RegistrationService, invites: InviteService, users: InMemoryUserRepository
) -> None:
    created = await invites.create(created_by="admin-1", role=Role.ADMIN)
    user = await service.register(
        raw_token=created.raw_token, email="  New@Example.COM ", password=PASSWORD
    )
    assert user.role is Role.ADMIN
    assert user.email == "new@example.com"
    assert user.is_verified is False
    assert user.password_hash == f"hashed:{PASSWORD}"
    assert await users.get_by_email("new@example.com") is not None


async def test_invite_is_consumed_exactly_once(
    service: RegistrationService, invites: InviteService, invite_repo: InMemoryInviteRepository
) -> None:
    created = await invites.create(created_by="admin-1")
    await service.register(raw_token=created.raw_token, email="a@example.com", password=PASSWORD)
    stored = await invite_repo.get(created.invite.id)
    assert stored is not None
    assert stored.status(START) is InviteStatus.REDEEMED


async def test_weak_password_does_not_consume_the_invite(
    service: RegistrationService,
    invites: InviteService,
    invite_repo: InMemoryInviteRepository,
    users: InMemoryUserRepository,
) -> None:
    created = await invites.create(created_by="admin-1")
    with pytest.raises(WeakPassword):
        await service.register(raw_token=created.raw_token, email="a@example.com", password="short")
    stored = await invite_repo.get(created.invite.id)
    assert stored is not None
    assert stored.status(START) is InviteStatus.ACTIVE
    assert await users.get_by_email("a@example.com") is None


async def test_invalid_email_does_not_consume_the_invite(
    service: RegistrationService, invites: InviteService, invite_repo: InMemoryInviteRepository
) -> None:
    created = await invites.create(created_by="admin-1")
    with pytest.raises(InvalidEmail):
        await service.register(raw_token=created.raw_token, email="nope", password=PASSWORD)
    stored = await invite_repo.get(created.invite.id)
    assert stored is not None
    assert stored.status(START) is InviteStatus.ACTIVE


async def test_duplicate_email_is_rejected_before_consuming_a_second_invite(
    service: RegistrationService, invites: InviteService, invite_repo: InMemoryInviteRepository
) -> None:
    first = await invites.create(created_by="admin-1")
    await service.register(raw_token=first.raw_token, email="dup@example.com", password=PASSWORD)
    second = await invites.create(created_by="admin-1")
    with pytest.raises(EmailAlreadyRegistered):
        await service.register(
            raw_token=second.raw_token, email="Dup@example.com", password=PASSWORD
        )
    stored = await invite_repo.get(second.invite.id)
    assert stored is not None
    assert stored.status(START) is InviteStatus.ACTIVE


async def test_unknown_invite_is_rejected(service: RegistrationService) -> None:
    with pytest.raises(InviteNotFound):
        await service.register(raw_token="not-a-token", email="a@example.com", password=PASSWORD)


async def test_expired_invite_is_rejected(
    service: RegistrationService, invites: InviteService, clock: FakeClock
) -> None:
    created = await invites.create(created_by="admin-1", ttl=timedelta(hours=1))
    clock.advance(timedelta(hours=1))
    with pytest.raises(InviteExpired):
        await service.register(
            raw_token=created.raw_token, email="a@example.com", password=PASSWORD
        )


async def test_add_race_after_redeem_surfaces_as_already_registered(
    invites: InviteService, clock: FakeClock
) -> None:
    class RacingUserRepository(InMemoryUserRepository):
        """A rival signup inserted the same email between our pre-check and add."""

        async def get_by_email(self, email: str) -> User | None:
            return None

        async def add(self, user: User) -> None:
            raise EmailAlreadyRegistered(user.email)

    service = RegistrationService(invites, RacingUserRepository(), FakeHasher(), clock)
    created = await invites.create(created_by="admin-1")
    with pytest.raises(EmailAlreadyRegistered):
        await service.register(
            raw_token=created.raw_token, email="a@example.com", password=PASSWORD
        )
