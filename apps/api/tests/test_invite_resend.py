# SPDX-License-Identifier: AGPL-3.0-only
"""Invite emails and resending them with a new token (F-ADMIN-1)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from flashcards.adapters.memory import InMemoryInviteRepository
from flashcards.api.app import create_app
from flashcards.api.container import Container
from flashcards.application.invite_service import (
    CreatedInvite,
    InviteHasNoEmail,
    InviteService,
    invite_email,
)
from flashcards.config import Settings
from flashcards.domain.invites import Invite, InviteNotFound, Role
from flashcards.domain.mail import OutgoingEmail

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
PASSWORD = "correct-horse-battery"


class _Clock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        return self.value


def _token_in(email: OutgoingEmail) -> str:
    link = next(word for word in email.body.split() if word.startswith("http"))
    parsed = urlparse(link)
    assert parsed.path == "/signup" and parsed.query == ""  # never in the query string
    assert parsed.fragment.startswith("token=")
    return parsed.fragment.removeprefix("token=")


def test_a_link_only_invite_has_no_email() -> None:
    invite = Invite(id="i", token_hash="h", role=Role.MEMBER, created_by="a",
                    created_at=NOW, expires_at=NOW + timedelta(days=7))
    assert invite_email(CreatedInvite(invite=invite, raw_token="t"), base_url="https://x") is None


def test_the_invite_email_carries_the_link_and_expiry() -> None:
    invite = Invite(id="i", token_hash="h", role=Role.MEMBER, created_by="a",
                    created_at=NOW, expires_at=NOW + timedelta(days=7), email="a@example.com")
    email = invite_email(CreatedInvite(invite=invite, raw_token="tok"), base_url="https://x/")
    assert email is not None and email.to == "a@example.com"
    assert "https://x/signup#token=tok" in email.body and "2026-11-15 12:00 UTC" in email.body


async def test_rotating_replaces_the_token_of_an_active_emailed_invite() -> None:
    clock = _Clock()
    repo = InMemoryInviteRepository()
    service = InviteService(repo, clock)
    created = await service.create(created_by="admin", email="a@example.com")

    rotated = await service.rotate(created.invite.id)
    assert rotated is not None and rotated.raw_token != created.raw_token
    assert rotated.invite.expires_at == created.invite.expires_at  # expiry unchanged
    with pytest.raises(InviteNotFound):  # the old link no longer works
        await service.redeem(created.raw_token, email="a@example.com")
    redeemed = await service.redeem(rotated.raw_token, email="a@example.com")
    assert redeemed.id == created.invite.id

    # Spent, missing, or link-only invites cannot be rotated.
    assert await service.rotate(created.invite.id) is None
    assert await service.rotate("missing") is None
    link_only = await service.create(created_by="admin")
    with pytest.raises(InviteHasNoEmail):
        await service.rotate(link_only.invite.id)


async def test_revoked_and_expired_invites_cannot_be_rotated() -> None:
    clock = _Clock()
    service = InviteService(InMemoryInviteRepository(), clock)
    revoked = await service.create(created_by="admin", email="a@example.com")
    await service.revoke(revoked.invite.id)
    assert await service.rotate(revoked.invite.id) is None

    expiring = await service.create(created_by="admin", email="b@example.com",
                                    ttl=timedelta(hours=1))
    clock.value = NOW + timedelta(hours=1)
    assert await service.rotate(expiring.invite.id) is None


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


def _admin() -> tuple[TestClient, TestClient]:
    app = create_app(Settings(environment="test", session_cookie_secure=False))
    container: Container = app.state.container
    token = asyncio.run(container.invites.create(created_by="seed", role=Role.ADMIN)).raw_token
    admin = TestClient(app)
    response = admin.post(
        "/auth/signup", json={"token": token, "email": "admin@example.com", "password": PASSWORD}
    )
    assert response.status_code == 201
    return admin, TestClient(app)


def test_invites_with_an_email_are_sent_and_can_be_resent(mailer: _CapturingMailer) -> None:
    admin, newcomer = _admin()
    mailer.sent.clear()  # the admin's own verification email

    created = admin.post("/admin/invites", json={"email": "New@Example.com"}).json()
    assert created["email"] == "new@example.com"
    [first] = mailer.sent
    assert first.to == "new@example.com" and _token_in(first) == created["token"]

    resent = admin.post(f"/admin/invites/{created['id']}/resend")
    assert resent.status_code == 200
    body = resent.json()
    assert body["id"] == created["id"] and body["token"] != created["token"]
    assert body["expires_at"] == created["expires_at"]
    assert _token_in(mailer.sent[1]) == body["token"]

    old = newcomer.post("/auth/signup", json={
        "token": created["token"], "email": "new@example.com", "password": PASSWORD})
    assert old.status_code == 400
    new = newcomer.post("/auth/signup", json={
        "token": body["token"], "email": "new@example.com", "password": PASSWORD})
    assert new.status_code == 201

    # Redeemed now: nothing to resend.
    assert admin.post(f"/admin/invites/{created['id']}/resend").status_code == 404
    actions = [e["action"] for e in admin.get("/admin/audit").json()]
    assert "invite.resent" in actions


def test_resend_refuses_link_only_and_unknown_invites(mailer: _CapturingMailer) -> None:
    admin, _ = _admin()
    link_only = admin.post("/admin/invites", json={}).json()
    sent_before = len(mailer.sent)
    response = admin.post(f"/admin/invites/{link_only['id']}/resend")
    assert response.status_code == 409
    assert admin.post("/admin/invites/missing/resend").status_code == 404
    assert len(mailer.sent) == sent_before


def test_an_invite_email_must_be_a_valid_address(mailer: _CapturingMailer) -> None:
    admin, _ = _admin()
    sent_before = len(mailer.sent)
    response = admin.post("/admin/invites", json={"email": "a@example.com, b@evil.com"})
    assert response.status_code == 422
    assert len(mailer.sent) == sent_before


def test_members_cannot_resend(mailer: _CapturingMailer) -> None:
    admin, member = _admin()
    created = admin.post("/admin/invites", json={"email": "m@example.com"}).json()
    member.post("/auth/signup", json={
        "token": created["token"], "email": "m@example.com", "password": PASSWORD})
    assert member.post(f"/admin/invites/{created['id']}/resend").status_code == 403
