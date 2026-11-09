# SPDX-License-Identifier: AGPL-3.0-only
"""The device list: labels, last-seen and revoking sessions (F-AUTH-2)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.adapters.memory import (
    InMemoryAuditRepository,
    InMemorySessionRepository,
    InMemoryUserRepository,
)
from flashcards.api.app import create_app
from flashcards.api.container import Container
from flashcards.application.audit_service import AuditService
from flashcards.application.auth_service import AuthService
from flashcards.application.session_service import SessionService
from flashcards.config import Settings
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import Role
from flashcards.domain.sessions import SEEN_RESOLUTION, UNKNOWN_DEVICE, Session, device_label
from flashcards.domain.users import User

START = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
PASSWORD = "correct-horse-battery"
FIREFOX_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.1; rv:132.0) Gecko/20100101 Firefox/132.0"
)
CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class _Clock:
    def __init__(self) -> None:
        self.value = START

    def now(self) -> datetime:
        return self.value


class _Hasher:
    def hash(self, password: str) -> str:
        return f"hashed:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == f"hashed:{password}"


@pytest.mark.parametrize(
    ("agent", "label"),
    [
        (FIREFOX_MAC, "Firefox on macOS"),
        (CHROME_WINDOWS, "Chrome on Windows"),
        (CHROME_WINDOWS.replace("Safari/537.36", "Safari/537.36 Edg/130.0"), "Edge on Windows"),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "Safari on iOS",
        ),
        (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36",
            "Chrome on Android",
        ),
        ("Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) Chrome/130.0", "Chrome on ChromeOS"),
        ("Mozilla/5.0 (X11; Linux x86_64) OPR/110.0 Chrome/130.0", "Opera on Linux"),
        ("curl/8.4.0", UNKNOWN_DEVICE),
        ("SomeBot (Windows)", "Windows"),
        ("Firefox/132.0", "Firefox"),
        ("", UNKNOWN_DEVICE),
        (None, UNKNOWN_DEVICE),
    ],
)
def test_device_labels_use_a_fixed_vocabulary(agent: str | None, label: str) -> None:
    assert device_label(agent) == label


def test_a_label_never_echoes_the_user_agent() -> None:
    assert device_label("<script>alert(1)</script> Firefox/1 Windows") == "Firefox on Windows"


def _session(sid: str, user_id: str = "u1", **extra: datetime | None) -> Session:
    return Session(
        id=sid,
        user_id=user_id,
        token_hash=f"hash-{sid}",
        created_at=START,
        expires_at=START + timedelta(days=14),
        **extra,  # type: ignore[arg-type]
    )


def test_last_seen_is_recorded_at_most_once_per_interval() -> None:
    assert _session("s").needs_touch(START) is True  # never seen
    seen = _session("s").with_seen(START)
    assert seen.needs_touch(START + SEEN_RESOLUTION - timedelta(seconds=1)) is False
    assert seen.needs_touch(START + SEEN_RESOLUTION) is True


async def test_authenticate_records_last_seen_when_stale() -> None:
    clock = _Clock()
    sessions = InMemorySessionRepository()
    users = InMemoryUserRepository()
    auth = AuthService(users, sessions, _Hasher(), clock, dummy_hash="x")
    user = User(id="u1", email="a@example.com", password_hash="hashed:x", role=Role.MEMBER,
                created_at=START)
    issued = await auth.start_session(user, device="Firefox on macOS")
    assert issued.session.last_seen_at == START and issued.session.device == "Firefox on macOS"

    clock.value = START + timedelta(minutes=1)
    fresh = await auth.authenticate(issued.raw_token)
    assert fresh is not None and fresh.last_seen_at == START  # within the interval: no write

    clock.value = START + timedelta(minutes=6)
    touched = await auth.authenticate(issued.raw_token)
    assert touched is not None and touched.last_seen_at == clock.value
    stored = await sessions.list_for_user("u1")
    assert stored[0].last_seen_at == clock.value


async def test_touching_an_unknown_session_does_nothing() -> None:
    sessions = InMemorySessionRepository()
    await sessions.touch("missing", START)
    assert await sessions.list_for_user("u1") == []


def _service() -> tuple[SessionService, InMemorySessionRepository, InMemoryAuditRepository]:
    sessions = InMemorySessionRepository()
    events = InMemoryAuditRepository()
    clock = _Clock()
    return SessionService(sessions, AuditService(events, clock), clock), sessions, events


async def test_the_device_list_leaves_out_revoked_and_expired_sessions() -> None:
    service, sessions, _ = _service()
    await sessions.add(_session("live"))
    await sessions.add(_session("revoked", revoked_at=START))
    await sessions.add(
        Session(id="old", user_id="u1", token_hash="h-old", created_at=START - timedelta(days=30),
                expires_at=START - timedelta(days=16))
    )
    await sessions.add(_session("someone-else", user_id="u2"))
    assert [s.id for s in await service.active_for("u1")] == ["live"]


async def test_revoking_is_limited_to_the_users_own_live_sessions() -> None:
    service, sessions, events = _service()
    await sessions.add(_session("mine"))
    await sessions.add(_session("theirs", user_id="u2"))

    assert await service.revoke(user_id="u1", session_id="theirs") is False
    assert await service.revoke(user_id="u1", session_id="missing") is False
    assert await service.revoke(user_id="u1", session_id="mine") is True
    assert await service.revoke(user_id="u1", session_id="mine") is False  # no longer live

    recorded = await events.list_recent(limit=10)
    assert [(e.action, e.target_id) for e in recorded] == [
        (AuditAction.SESSION_REVOKED, "mine")
    ]


async def test_a_session_revoked_concurrently_is_not_reported_twice() -> None:
    service, sessions, events = _service()
    await sessions.add(_session("mine"))

    class _Racing(InMemorySessionRepository):
        async def revoke(self, session_id: str, now: datetime) -> bool:
            return False  # another request revoked it between the check and the write

    racing = _Racing()
    await racing.add(_session("mine"))
    racing_service = SessionService(racing, AuditService(events, _Clock()), _Clock())
    assert await racing_service.revoke(user_id="u1", session_id="mine") is False
    assert await events.list_recent(limit=10) == []


async def test_revoke_others_keeps_the_current_session() -> None:
    service, sessions, events = _service()
    for sid in ("current", "laptop", "phone"):
        await sessions.add(_session(sid))
    await sessions.add(_session("gone", revoked_at=START))
    await sessions.add(_session("theirs", user_id="u2"))

    assert await service.revoke_others(user_id="u1", current_session_id="current") == 2
    assert [s.id for s in await service.active_for("u1")] == ["current"]
    assert [s.id for s in await service.active_for("u2")] == ["theirs"]
    recorded = await events.list_recent(limit=10)
    assert [(e.action, dict(e.detail)) for e in recorded] == [
        (AuditAction.SESSIONS_REVOKED_OTHERS, {"count": "2"})
    ]

    # Nothing left to revoke: nothing recorded.
    assert await service.revoke_others(user_id="u1", current_session_id="current") == 0
    assert len(await events.list_recent(limit=10)) == 1


def _member(app: FastAPI, agent: str) -> TestClient:
    container: Container = app.state.container
    token = asyncio.run(container.invites.create(created_by="seed", role=Role.MEMBER)).raw_token
    client = TestClient(app, headers={"User-Agent": agent})
    response = client.post(
        "/auth/signup", json={"token": token, "email": "m@example.com", "password": PASSWORD}
    )
    assert response.status_code == 201
    return client


def test_the_device_list_over_http() -> None:
    app = create_app(Settings(environment="test", session_cookie_secure=False))
    laptop = _member(app, FIREFOX_MAC)
    desktop = TestClient(app, headers={"User-Agent": CHROME_WINDOWS})
    login = desktop.post("/auth/login", json={"email": "m@example.com", "password": PASSWORD})
    assert login.status_code == 200

    listed = desktop.get("/auth/sessions").json()
    assert {(s["device"], s["current"]) for s in listed} == {
        ("Firefox on macOS", False),
        ("Chrome on Windows", True),
    }
    for entry in listed:
        assert entry["last_seen_at"] is not None
        # Only these fields: never the token or its hash.
        assert set(entry) == {"id", "created_at", "expires_at", "last_seen_at", "device", "current"}

    revoked = desktop.post("/auth/sessions/revoke-others")
    assert revoked.status_code == 200 and revoked.json() == {"revoked": 1}
    assert laptop.get("/auth/me").status_code == 401
    assert desktop.get("/auth/me").status_code == 200
    assert [s["device"] for s in desktop.get("/auth/sessions").json()] == ["Chrome on Windows"]


def test_revoke_others_requires_sign_in() -> None:
    app = create_app(Settings(environment="test", session_cookie_secure=False))
    assert TestClient(app).post("/auth/sessions/revoke-others").status_code == 401
