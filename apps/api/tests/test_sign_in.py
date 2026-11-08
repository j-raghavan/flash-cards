# SPDX-License-Identifier: AGPL-3.0-only
"""Failed sign-in throttling and lockout (F-AUTH-6)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.adapters.memory import (
    InMemoryAuditRepository,
    InMemoryLoginThrottleRepository,
    InMemorySessionRepository,
    InMemoryUserRepository,
)
from flashcards.application.audit_service import AuditService
from flashcards.application.auth_service import AccountSuspended, AuthService, InvalidCredentials
from flashcards.application.sign_in_service import SignInService, throttle_key
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import Role
from flashcards.domain.login_throttle import (
    FAILURE_WINDOW,
    LOCKOUT,
    MAX_FAILED_LOGINS,
    LoginLocked,
    ThrottleState,
    admitted,
    after_attempt,
)
from flashcards.domain.users import User

START = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
PASSWORD = "correct-horse-battery"
EMAIL = "person@example.com"


class _Clock:
    def __init__(self) -> None:
        self.value = START

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class _Hasher:
    def hash(self, password: str) -> str:
        return f"hashed:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == f"hashed:{password}"


def test_attempts_lock_at_the_limit_and_never_extend_the_lock() -> None:
    state: ThrottleState | None = None
    for attempt in range(1, MAX_FAILED_LOGINS + 1):
        state = after_attempt(state, "k", START)
        assert admitted(state) and state.failures == attempt
    assert state is not None and state.locked_for(START) == LOCKOUT  # the 5th starts it
    assert state.locked_for(START + LOCKOUT) is None  # and it ends on time

    later = START + timedelta(minutes=5)
    refused = after_attempt(state, "k", later)
    assert not admitted(refused)
    assert refused.locked_until == state.locked_until  # refused attempts do not extend it

    after_lock = after_attempt(refused, "k", START + LOCKOUT)
    assert admitted(after_lock) and after_lock.failures == 1 and after_lock.locked_until is None


def test_an_old_window_starts_counting_again() -> None:
    state = after_attempt(after_attempt(None, "k", START), "k", START)
    assert state.failures == 2
    assert after_attempt(state, "k", START + FAILURE_WINDOW).failures == 1


class _Rig:
    def __init__(self) -> None:
        self.clock = _Clock()
        self.users = InMemoryUserRepository()
        self.throttle = InMemoryLoginThrottleRepository()
        self.events = InMemoryAuditRepository()
        auth = AuthService(self.users, InMemorySessionRepository(), _Hasher(), self.clock)
        audit = AuditService(self.events, self.clock)
        self.sign_in = SignInService(auth, self.throttle, self.users, audit, self.clock)

    async def add_user(self, *, suspended: bool = False) -> User:
        user = User(
            id="u1", email=EMAIL, password_hash=f"hashed:{PASSWORD}", role=Role.MEMBER,
            created_at=START,
        )
        user = user.with_suspended(START) if suspended else user
        await self.users.add(user)
        return user

    async def fail(self, email: str = EMAIL, times: int = MAX_FAILED_LOGINS) -> None:
        for _ in range(times):
            with pytest.raises(InvalidCredentials):
                await self.sign_in.login(email=email, password="wrong")


async def test_a_locked_email_refuses_even_the_right_password() -> None:
    rig = _Rig()
    user = await rig.add_user()
    await rig.fail()

    with pytest.raises(LoginLocked) as caught:
        await rig.sign_in.login(email=EMAIL, password=PASSWORD)
    assert caught.value.retry_after == LOCKOUT
    # Only the moment the lock started is audited, against the account.
    events = await rig.events.list_recent(limit=10)
    assert [(e.action, e.target_id) for e in events] == [(AuditAction.LOGIN_LOCKED, user.id)]

    rig.clock.advance(LOCKOUT)
    assert (await rig.sign_in.login(email=EMAIL, password=PASSWORD)).session.user_id == user.id


async def test_success_clears_the_count() -> None:
    rig = _Rig()
    await rig.add_user()
    await rig.fail(times=MAX_FAILED_LOGINS - 1)
    await rig.sign_in.login(email=EMAIL, password=PASSWORD)
    assert await rig.throttle.get(throttle_key(EMAIL)) is None
    await rig.fail(times=MAX_FAILED_LOGINS - 1)  # a fresh count: still not locked
    await rig.sign_in.login(email=EMAIL, password=PASSWORD)


async def test_unknown_emails_lock_the_same_way() -> None:
    rig = _Rig()
    await rig.fail(email="nobody@example.com")
    with pytest.raises(LoginLocked):
        await rig.sign_in.login(email="nobody@example.com", password="anything")
    events = await rig.events.list_recent(limit=10)
    assert [(e.action, e.target_id) for e in events] == [(AuditAction.LOGIN_LOCKED, None)]


async def test_the_key_is_case_insensitive_and_never_the_address() -> None:
    key = throttle_key("  Person@Example.COM ")
    assert key == throttle_key(EMAIL) and EMAIL not in key


async def test_a_suspended_account_is_not_counted() -> None:
    rig = _Rig()
    await rig.add_user(suspended=True)
    with pytest.raises(AccountSuspended):
        await rig.sign_in.login(email=EMAIL, password=PASSWORD)
    assert await rig.throttle.get(throttle_key(EMAIL)) is None


async def test_concurrent_guesses_are_each_counted() -> None:
    rig = _Rig()
    await rig.add_user()

    async def guess() -> str:
        try:
            await rig.sign_in.login(email=EMAIL, password="wrong")
        except InvalidCredentials:
            return "checked"
        except LoginLocked:
            return "refused"
        raise AssertionError("a wrong password signed in")  # pragma: no cover

    outcomes = await asyncio.gather(*(guess() for _ in range(50)))

    # However many arrive at once, only the limit's worth reach the password check.
    assert outcomes.count("checked") == MAX_FAILED_LOGINS
    assert outcomes.count("refused") == 50 - MAX_FAILED_LOGINS


def test_lockout_over_http(app: FastAPI, seed_invite: Callable[..., str]) -> None:
    client = TestClient(app)
    signup = client.post(
        "/auth/signup", json={"token": seed_invite(), "email": EMAIL, "password": PASSWORD}
    )
    assert signup.status_code == 201
    wrong = {"email": EMAIL, "password": "not-it"}
    for _ in range(MAX_FAILED_LOGINS):
        response = client.post("/auth/login", json=wrong)
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid email or password."

    locked = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})

    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) == int(LOCKOUT.total_seconds())
