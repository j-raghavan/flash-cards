# SPDX-License-Identifier: AGPL-3.0-only
"""Email verification and password reset (F-AUTH-2), service and HTTP."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.adapters.mailers import LogMailer, SmtpMailer
from flashcards.adapters.memory import (
    InMemoryAuditRepository,
    InMemoryLoginThrottleRepository,
    InMemorySessionRepository,
    InMemoryUserRepository,
    InMemoryUserTokenRepository,
)
from flashcards.api.app import _mailer_from_settings, create_app
from flashcards.api.container import Container
from flashcards.application.account_email_service import AccountEmailService
from flashcards.application.audit_service import AuditService
from flashcards.application.sign_in_service import throttle_key
from flashcards.config import Settings
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import Role
from flashcards.domain.login_throttle import ThrottleState
from flashcards.domain.mail import OutgoingEmail
from flashcards.domain.sessions import Session
from flashcards.domain.user_tokens import (
    PASSWORD_RESET_TTL,
    RESET_COOLDOWN,
    VERIFICATION_TTL,
    InvalidUserToken,
    TokenPurpose,
    UserToken,
)
from flashcards.domain.users import User, WeakPassword

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


def _token_from(email: OutgoingEmail) -> str:
    link = next(word for word in email.body.split() if word.startswith("http"))
    return parse_qs(urlparse(link).query)["token"][0]


def test_tokens_are_usable_once_for_their_purpose_until_they_expire() -> None:
    token = UserToken(
        id="t", user_id="u", purpose=TokenPurpose.PASSWORD_RESET, token_hash="h",
        created_at=START, expires_at=START + PASSWORD_RESET_TTL,
    )
    assert token.is_usable(TokenPurpose.PASSWORD_RESET, START)
    assert not token.is_usable(TokenPurpose.EMAIL_VERIFICATION, START)
    assert not token.is_usable(TokenPurpose.PASSWORD_RESET, START + PASSWORD_RESET_TTL)


class _Rig:
    def __init__(self) -> None:
        self.clock = _Clock()
        self.users = InMemoryUserRepository()
        self.tokens = InMemoryUserTokenRepository()
        self.sessions = InMemorySessionRepository()
        self.throttle = InMemoryLoginThrottleRepository()
        self.events = InMemoryAuditRepository()
        self.service = AccountEmailService(
            self.users, self.tokens, self.sessions, self.throttle, _Hasher(),
            AuditService(self.events, self.clock), self.clock,
            base_url="https://cards.example.com/",
        )

    async def user(self, *, suspended: bool = False, verified: bool = False) -> User:
        user = User(
            id="u1", email=EMAIL, password_hash=f"hashed:{PASSWORD}", role=Role.MEMBER,
            created_at=START,
        )
        user = user.with_suspended(START) if suspended else user
        user = user.with_verified(START) if verified else user
        await self.users.add(user)
        return user

    async def actions(self) -> list[AuditAction]:
        return [e.action for e in await self.events.list_recent(limit=20)]


async def test_verification_link_marks_the_email_verified_once() -> None:
    rig = _Rig()
    user = await rig.user()
    email = await rig.service.verification_email(user)
    assert email is not None and email.to == EMAIL
    assert "https://cards.example.com/verify-email?token=" in email.body

    verified = await rig.service.verify_email(_token_from(email))

    assert verified.is_verified
    stored = await rig.users.get(user.id)
    assert stored is not None and stored.is_verified
    assert await rig.actions() == [AuditAction.EMAIL_VERIFIED]
    with pytest.raises(InvalidUserToken):  # single use
        await rig.service.verify_email(_token_from(email))
    # Nothing more to send once verified.
    assert await rig.service.verification_email(stored) is None


async def test_a_new_verification_email_invalidates_the_previous_link() -> None:
    rig = _Rig()
    user = await rig.user()
    first = await rig.service.verification_email(user)
    second = await rig.service.verification_email(user)
    assert first is not None and second is not None
    with pytest.raises(InvalidUserToken):
        await rig.service.verify_email(_token_from(first))
    await rig.service.verify_email(_token_from(second))


async def test_expired_unknown_and_wrong_purpose_links_are_invalid() -> None:
    rig = _Rig()
    user = await rig.user()
    email = await rig.service.verification_email(user)
    assert email is not None
    with pytest.raises(InvalidUserToken):
        await rig.service.verify_email("not-a-token")
    with pytest.raises(InvalidUserToken):  # a verification link cannot reset a password
        await rig.service.reset_password(_token_from(email), "a-new-long-password")
    rig.clock.advance(VERIFICATION_TTL)
    with pytest.raises(InvalidUserToken):
        await rig.service.verify_email(_token_from(email))


async def test_a_link_for_a_suspended_or_deleted_account_is_invalid() -> None:
    rig = _Rig()
    user = await rig.user()
    email = await rig.service.verification_email(user)
    assert email is not None
    await rig.users.update(user.with_suspended(START))
    with pytest.raises(InvalidUserToken):
        await rig.service.verify_email(_token_from(email))

    again = _Rig()
    other = await again.user()
    link = await again.service.verification_email(other)
    assert link is not None
    await again.users.delete(other.id)
    with pytest.raises(InvalidUserToken):
        await again.service.verify_email(_token_from(link))


class _LosesTheRace(InMemoryUserTokenRepository):
    """Reads the token as unused, but a concurrent request marks it used first."""

    async def try_use(self, token_id: str, now: datetime) -> bool:
        return False


async def test_a_concurrent_use_of_the_same_link_wins_once() -> None:
    rig = _Rig()
    rig.tokens = _LosesTheRace()
    rig.service._tokens = rig.tokens  # the rig's service shares this repository
    user = await rig.user()
    email = await rig.service.verification_email(user)
    assert email is not None
    with pytest.raises(InvalidUserToken):
        await rig.service.verify_email(_token_from(email))


async def test_a_link_for_an_already_verified_email_succeeds_quietly() -> None:
    rig = _Rig()
    user = await rig.user()
    email = await rig.service.verification_email(user)
    assert email is not None
    await rig.users.update(user.with_verified(START))  # verified another way meanwhile
    assert (await rig.service.verify_email(_token_from(email))).is_verified
    assert await rig.actions() == []  # nothing new to record


async def test_password_reset_changes_the_password_and_ends_every_session() -> None:
    rig = _Rig()
    user = await rig.user()
    for sid in ("s1", "s2"):
        await rig.sessions.add(
            Session(
                id=sid, user_id=user.id, token_hash=sid, created_at=START,
                expires_at=START + timedelta(days=1),
            )
        )
    await rig.throttle.save(
        ThrottleState(key=throttle_key(EMAIL), failures=3, window_started_at=START)
    )
    email = await rig.service.password_reset_email("  Person@Example.com ")
    assert email is not None and "reset-password?token=" in email.body

    await rig.service.reset_password(_token_from(email), "a-brand-new-password")

    stored = await rig.users.get(user.id)
    assert stored is not None and stored.password_hash == "hashed:a-brand-new-password"
    assert all(s.revoked_at is not None for s in await rig.sessions.list_for_user(user.id))
    assert await rig.throttle.get(throttle_key(EMAIL)) is None
    assert await rig.actions() == [
        AuditAction.PASSWORD_RESET_COMPLETED,
        AuditAction.PASSWORD_RESET_REQUESTED,
    ]
    with pytest.raises(InvalidUserToken):
        await rig.service.reset_password(_token_from(email), "another-new-password")


async def test_a_weak_password_does_not_spend_the_reset_link() -> None:
    rig = _Rig()
    await rig.user()
    email = await rig.service.password_reset_email(EMAIL)
    assert email is not None
    with pytest.raises(WeakPassword):
        await rig.service.reset_password(_token_from(email), "short")
    await rig.service.reset_password(_token_from(email), "a-long-enough-password")


async def test_reset_emails_are_not_sent_for_unknown_or_suspended_accounts() -> None:
    rig = _Rig()
    assert await rig.service.password_reset_email("nobody@example.com") is None
    await rig.user(suspended=True)
    assert await rig.service.password_reset_email(EMAIL) is None
    assert await rig.actions() == []


async def test_reset_emails_respect_the_cooldown() -> None:
    rig = _Rig()
    await rig.user()
    assert await rig.service.password_reset_email(EMAIL) is not None
    assert await rig.service.password_reset_email(EMAIL) is None
    rig.clock.advance(RESET_COOLDOWN)
    assert await rig.service.password_reset_email(EMAIL) is not None


async def test_log_mailer_hides_content_outside_development(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mail = OutgoingEmail(to=EMAIL, subject="Reset", body="https://x/reset?token=SECRET")
    with caplog.at_level(logging.WARNING):
        await LogMailer(show_content=False).send(mail)
    assert "SECRET" not in caplog.text and "Reset" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        await LogMailer(show_content=True).send(mail)
    assert "SECRET" in caplog.text


class _FakeSmtp:
    instances: list[_FakeSmtp] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.address = (host, port, timeout)
        self.calls: list[str] = []
        self.sent: list[str] = []
        _FakeSmtp.instances.append(self)

    def __enter__(self) -> _FakeSmtp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append("quit")

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        self.calls.append(f"login:{username}")

    def send_message(self, message: object) -> None:
        self.sent.append(str(message))


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[_FakeSmtp]]:
    _FakeSmtp.instances = []
    monkeypatch.setattr("flashcards.adapters.mailers.smtplib.SMTP", _FakeSmtp)
    yield _FakeSmtp


async def test_smtp_mailer_sends_with_tls_and_login(fake_smtp: type[_FakeSmtp]) -> None:
    mailer = SmtpMailer(
        host="smtp.example.com", port=587, sender="Cards <no-reply@example.com>",
        username="user", password="pw",
    )
    await mailer.send(OutgoingEmail(to=EMAIL, subject="Hello", body="Body text"))
    smtp = fake_smtp.instances[0]
    assert smtp.address == ("smtp.example.com", 587, 30.0)
    assert smtp.calls == ["starttls", "login:user", "quit"]
    assert "Subject: Hello" in smtp.sent[0] and "To: person@example.com" in smtp.sent[0]


async def test_smtp_mailer_without_tls_or_credentials(fake_smtp: type[_FakeSmtp]) -> None:
    mailer = SmtpMailer(host="localhost", port=25, sender="a@b.c", starttls=False)
    await mailer.send(OutgoingEmail(to=EMAIL, subject="Hi", body="x"))
    assert fake_smtp.instances[0].calls == ["quit"]


def test_mailer_selection_from_settings(caplog: pytest.LogCaptureFixture) -> None:
    smtp = _mailer_from_settings(Settings(environment="test", smtp_host="smtp.example.com"))
    assert isinstance(smtp, SmtpMailer)
    with caplog.at_level(logging.WARNING):
        assert isinstance(_mailer_from_settings(Settings(environment="production")), LogMailer)
    assert "No SMTP server configured" in caplog.text
    assert isinstance(_mailer_from_settings(Settings(environment="development")), LogMailer)


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


def _signed_up(app: FastAPI) -> TestClient:
    container: Container = app.state.container
    token = asyncio.run(container.invites.create(created_by="seed", role=Role.MEMBER)).raw_token
    client = TestClient(app)
    resp = client.post("/auth/signup", json={"token": token, "email": EMAIL, "password": PASSWORD})
    assert resp.status_code == 201 and resp.json()["email_verified"] is False
    return client


def test_signup_sends_a_verification_link_that_works(mailer: _CapturingMailer) -> None:
    app = create_app(Settings(environment="test", session_cookie_secure=False))
    client = _signed_up(app)
    assert [m.subject for m in mailer.sent] == ["Confirm your email address"]

    link_token = _token_from(mailer.sent[0])
    assert client.post("/auth/verify-email", json={"token": link_token}).status_code == 204
    assert client.get("/auth/me").json()["email_verified"] is True
    bad = client.post("/auth/verify-email", json={"token": "nope"})
    assert bad.status_code == 422 and bad.json()["detail"] == "This link is invalid or has expired."
    # Already verified: resending is accepted but sends nothing.
    assert client.post("/auth/verify-email/resend").status_code == 202
    assert len(mailer.sent) == 1


def test_resend_requires_sign_in(mailer: _CapturingMailer) -> None:
    app = create_app(Settings(environment="test", session_cookie_secure=False))
    assert TestClient(app).post("/auth/verify-email/resend").status_code == 401


def test_password_reset_over_http(mailer: _CapturingMailer) -> None:
    app = create_app(Settings(environment="test", session_cookie_secure=False))
    client = _signed_up(app)
    anonymous = TestClient(app)

    # Same answer for a known and an unknown email; only the known one gets mail.
    assert anonymous.post("/auth/password-reset", json={"email": "nobody@x.com"}).status_code == 202
    assert anonymous.post("/auth/password-reset", json={"email": EMAIL}).status_code == 202
    reset_mail = mailer.sent[-1]
    assert reset_mail.subject == "Reset your password"
    token = _token_from(reset_mail)

    weak = anonymous.post("/auth/password-reset/confirm", json={"token": token, "password": "x"})
    assert weak.status_code == 422
    done = anonymous.post(
        "/auth/password-reset/confirm", json={"token": token, "password": "a-new-long-password"}
    )
    assert done.status_code == 204
    # The old session no longer works; the new password does.
    assert client.get("/auth/me").status_code == 401
    login = anonymous.post("/auth/login", json={"email": EMAIL, "password": "a-new-long-password"})
    assert login.status_code == 200
    again = anonymous.post(
        "/auth/password-reset/confirm", json={"token": token, "password": "a-third-password"}
    )
    assert again.status_code == 422
