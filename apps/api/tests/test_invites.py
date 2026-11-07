# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import InMemoryInviteRepository
from flashcards.application.invite_service import InviteService
from flashcards.domain.invites import (
    InviteAlreadyRedeemed,
    InviteEmailMismatch,
    InviteExpired,
    InviteNotFound,
    InviteRevoked,
    InviteStatus,
    Role,
    hash_token,
)

START = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = START) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def repo() -> InMemoryInviteRepository:
    return InMemoryInviteRepository()


@pytest.fixture
def service(repo: InMemoryInviteRepository, clock: FakeClock) -> InviteService:
    return InviteService(repo, clock)


def test_create_stores_only_the_hash(
    service: InviteService, repo: InMemoryInviteRepository
) -> None:
    created = service.create(created_by="admin-1")
    stored = repo.get(created.invite.id)
    assert stored is not None
    assert stored.token_hash == hash_token(created.raw_token)
    assert created.raw_token not in repr(stored)


def test_tokens_are_unique_and_long(service: InviteService) -> None:
    tokens = {service.create(created_by="admin-1").raw_token for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 40 for t in tokens)


def test_default_expiry_is_seven_days(service: InviteService) -> None:
    created = service.create(created_by="admin-1")
    assert created.invite.expires_at == START + timedelta(days=7)


def test_non_positive_ttl_is_rejected(service: InviteService) -> None:
    with pytest.raises(ValueError):
        service.create(created_by="admin-1", ttl=timedelta(0))


def test_redeem_succeeds_once_and_keeps_role(service: InviteService) -> None:
    created = service.create(created_by="admin-1", role=Role.ADMIN)
    redeemed = service.redeem(created.raw_token)
    assert redeemed.role is Role.ADMIN
    assert redeemed.status(START) is InviteStatus.REDEEMED


def test_second_redeem_fails(service: InviteService) -> None:
    created = service.create(created_by="admin-1")
    service.redeem(created.raw_token)
    with pytest.raises(InviteAlreadyRedeemed):
        service.redeem(created.raw_token)


def test_unknown_token_fails(service: InviteService) -> None:
    with pytest.raises(InviteNotFound):
        service.redeem("not-a-real-token")


def test_expired_invite_fails(service: InviteService, clock: FakeClock) -> None:
    created = service.create(created_by="admin-1", ttl=timedelta(hours=1))
    clock.advance(timedelta(hours=1))
    with pytest.raises(InviteExpired):
        service.redeem(created.raw_token)


def test_revoked_invite_fails(service: InviteService) -> None:
    created = service.create(created_by="admin-1")
    assert service.revoke(created.invite.id) is True
    with pytest.raises(InviteRevoked):
        service.redeem(created.raw_token)


def test_cannot_revoke_a_redeemed_invite(service: InviteService) -> None:
    created = service.create(created_by="admin-1")
    service.redeem(created.raw_token)
    assert service.revoke(created.invite.id) is False


def test_email_bound_invite_requires_matching_email(service: InviteService) -> None:
    created = service.create(created_by="admin-1", email="Friend@Example.com")
    with pytest.raises(InviteEmailMismatch):
        service.redeem(created.raw_token, email="someone-else@example.com")
    with pytest.raises(InviteEmailMismatch):
        service.redeem(created.raw_token, email=None)
    # A failed attempt must not consume the invite.
    redeemed = service.redeem(created.raw_token, email="  friend@example.COM ")
    assert redeemed.redeemed_at == START


def test_open_invite_accepts_any_email(service: InviteService) -> None:
    created = service.create(created_by="admin-1")
    service.redeem(created.raw_token, email="anyone@example.com")


def test_losing_the_redeem_race_is_reported_as_already_redeemed(clock: FakeClock) -> None:
    class RacingRepository(InMemoryInviteRepository):
        """Simulates a rival signup that wins between our read and our write."""

        def try_redeem(self, invite_id: str, now: datetime) -> bool:
            super().try_redeem(invite_id, now)  # the rival wins first
            return super().try_redeem(invite_id, now)  # our attempt loses

    service = InviteService(RacingRepository(), clock)
    created = service.create(created_by="admin-1")
    with pytest.raises(InviteAlreadyRedeemed):
        service.redeem(created.raw_token)


def test_concurrent_redeems_allow_exactly_one_winner(
    service: InviteService,
) -> None:
    created = service.create(created_by="admin-1")
    results: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def attempt() -> None:
        barrier.wait()
        try:
            service.redeem(created.raw_token)
            outcome = "ok"
        except InviteAlreadyRedeemed:
            outcome = "lost"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 1
    assert results.count("lost") == 7
