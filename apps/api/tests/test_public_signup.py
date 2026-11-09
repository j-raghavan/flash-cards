# SPDX-License-Identifier: AGPL-3.0-only
"""Public sign-up when INVITE_ONLY is off (F-AUTH-7)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from flashcards.adapters.memory import (
    InMemoryAuditRepository,
    InMemoryLoginThrottleRepository,
    InMemoryUserRepository,
)
from flashcards.api.app import create_app
from flashcards.api.container import Container
from flashcards.application.audit_service import AuditService
from flashcards.application.public_signup_service import PublicSignupService
from flashcards.config import Settings
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import Role
from flashcards.domain.login_throttle import MAX_FAILED_LOGINS, LoginLocked
from flashcards.domain.mail import OutgoingEmail
from flashcards.domain.users import InvalidEmail, User, WeakPassword

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
PASSWORD = "correct-horse-battery"


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Hasher:
    def __init__(self) -> None:
        self.hashed = 0

    def hash(self, password: str) -> str:
        self.hashed += 1
        return f"hashed:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == f"hashed:{password}"  # pragma: no cover - unused here


_Parts = tuple[PublicSignupService, InMemoryUserRepository, InMemoryAuditRepository, "_Hasher"]


def _service() -> _Parts:
    users = InMemoryUserRepository()
    events = InMemoryAuditRepository()
    hasher = _Hasher()
    service = PublicSignupService(
        users, hasher, InMemoryLoginThrottleRepository(), AuditService(events, _Clock()), _Clock()
    )
    return service, users, events, hasher


async def test_a_new_email_gets_an_unverified_member_account_and_an_audit_event() -> None:
    service, users, events, _ = _service()
    outcome = await service.register(email=" New@Example.com ", password=PASSWORD, client="1.2.3.4")
    assert outcome.created is True
    user = outcome.user
    assert user.email == "new@example.com" and user.role is Role.MEMBER
    assert user.is_verified is False
    assert await users.get_by_email("new@example.com") == user
    [event] = await events.list_recent(limit=5)
    assert event.action is AuditAction.USER_REGISTERED and event.target_id == user.id
    assert dict(event.detail) == {"via": "public"}


async def test_an_existing_email_is_reported_but_not_changed_and_costs_the_same_work() -> None:
    service, users, events, hasher = _service()
    existing = User(id="u1", email="taken@example.com", password_hash="hashed:old",
                    role=Role.ADMIN, created_at=NOW)
    await users.add(existing)
    outcome = await service.register(email="taken@example.com", password=PASSWORD, client="c")
    assert outcome.created is False and outcome.user == existing
    assert await users.get("u1") == existing  # untouched
    assert hasher.hashed == 1  # hashed anyway, so the timing matches a new account
    assert await events.list_recent(limit=5) == []


async def test_bad_input_is_refused_after_the_attempt_is_counted() -> None:
    service, _, _, _ = _service()
    with pytest.raises(InvalidEmail):
        await service.register(email="not-an-email", password=PASSWORD, client="c")
    with pytest.raises(WeakPassword):
        await service.register(email="a@example.com", password="short", client="c")
    for _ in range(MAX_FAILED_LOGINS - 2):
        await service.register(email="b@example.com", password=PASSWORD, client="c")
    with pytest.raises(LoginLocked):
        await service.register(email="c@example.com", password=PASSWORD, client="c")
    # Another client is counted separately.
    outcome = await service.register(email="d@example.com", password=PASSWORD, client="other")
    assert outcome.created is True


class _CapturingMailer:
    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []

    async def send(self, email: OutgoingEmail) -> None:
        self.sent.append(email)


@pytest.fixture
def mailer(monkeypatch: pytest.MonkeyPatch) -> _CapturingMailer:
    capturing = _CapturingMailer()
    monkeypatch.setattr("flashcards.api.app._mailer_from_settings", lambda _s: capturing)
    return capturing


def _open_app() -> TestClient:
    return TestClient(
        create_app(Settings(environment="test", session_cookie_secure=False, invite_only=False))
    )


def test_registration_is_not_offered_on_an_invite_only_instance() -> None:
    client = TestClient(create_app(Settings(environment="test", session_cookie_secure=False)))
    assert client.get("/auth/options").json() == {"invite_only": True}
    response = client.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    assert response.status_code == 404


def test_open_registration_answers_the_same_for_new_and_existing_emails(
    mailer: _CapturingMailer,
) -> None:
    client = _open_app()
    assert client.get("/auth/options").json() == {"invite_only": False}

    new = client.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    again = client.post(
        "/auth/register", json={"email": "a@example.com", "password": "other-pw-123"}
    )
    assert new.status_code == again.status_code == 202
    assert new.json() == again.json()
    assert "set-cookie" not in new.headers  # no sign-in until the owner signs in

    assert [m.subject for m in mailer.sent] == [
        "Confirm your email address",
        "You already have a Flash-Cards account",
    ]
    assert all(m.to == "a@example.com" for m in mailer.sent)

    # The new account signs in with the first password, not the second.
    login = client.post("/auth/login", json={"email": "a@example.com", "password": PASSWORD})
    assert login.status_code == 200 and login.json()["role"] == "member"


def test_a_suspended_account_gets_no_notice(mailer: _CapturingMailer) -> None:
    app = create_app(
        Settings(environment="test", session_cookie_secure=False, invite_only=False)
    )
    container: Container = app.state.container
    user = User(id="s1", email="s@example.com", password_hash="x", role=Role.MEMBER,
                created_at=NOW, suspended_at=NOW)
    asyncio.run(container.user_repo.add(user))
    client = TestClient(app)
    response = client.post("/auth/register", json={"email": "s@example.com", "password": PASSWORD})
    assert response.status_code == 202
    assert mailer.sent == []


def test_registration_refusals_are_returned_and_throttled(mailer: _CapturingMailer) -> None:
    client = _open_app()
    weak = client.post("/auth/register", json={"email": "a@example.com", "password": "short"})
    assert weak.status_code == 422
    bad = client.post("/auth/register", json={"email": "nope", "password": PASSWORD})
    assert bad.status_code == 422
    for i in range(MAX_FAILED_LOGINS - 2):
        ok = client.post(
            "/auth/register", json={"email": f"u{i}@example.com", "password": PASSWORD}
        )
        assert ok.status_code == 202
    limited = client.post("/auth/register", json={"email": "z@example.com", "password": PASSWORD})
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
