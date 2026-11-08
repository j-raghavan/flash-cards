# SPDX-License-Identifier: AGPL-3.0-only
"""The request's unit of work ends before the response; background mail runs after."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.api.app import create_app
from flashcards.api.container import Container
from flashcards.config import Settings
from flashcards.domain.invites import Role
from flashcards.domain.mail import OutgoingEmail

PASSWORD = "correct-horse-battery"


class _RecordingMailer:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self._events = events
        self._fail = fail

    async def send(self, email: OutgoingEmail) -> None:
        self._events.append("send")
        if self._fail:
            raise ConnectionError("SMTP is down")


def _app(monkeypatch: pytest.MonkeyPatch, mailer: _RecordingMailer, events: list[str]) -> FastAPI:
    monkeypatch.setattr("flashcards.api.app._mailer_from_settings", lambda _s: mailer)
    app = create_app(Settings(environment="test", session_cookie_secure=False))
    real = app.state.services_provider

    async def recording() -> AsyncIterator[Container]:
        # Mirrors the SQL provider: code after the yield is where it commits, and an
        # exception thrown into the yield is where it would roll back.
        try:
            async for container in real():
                yield container
        except BaseException:
            events.append("rollback")
            raise
        events.append("commit")

    app.state.services_provider = recording
    return app


def _signup(app: FastAPI) -> int:
    container: Container = app.state.container
    token = asyncio.run(container.invites.create(created_by="seed", role=Role.MEMBER)).raw_token
    resp = TestClient(app).post(
        "/auth/signup", json={"token": token, "email": "a@example.com", "password": PASSWORD}
    )
    return resp.status_code


def test_the_transaction_ends_before_the_email_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    app = _app(monkeypatch, _RecordingMailer(events), events)
    assert _signup(app) == 201
    # The token in the email is committed before the email can be opened.
    assert events == ["commit", "send"]


def test_a_mail_failure_neither_fails_nor_rolls_back_the_request(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[str] = []
    app = _app(monkeypatch, _RecordingMailer(events, fail=True), events)
    with caplog.at_level(logging.ERROR):
        assert _signup(app) == 201
    assert events == ["commit", "send"]
    assert "could not send email" in caplog.text
    # The account really exists.
    client = TestClient(app)
    login = client.post("/auth/login", json={"email": "a@example.com", "password": PASSWORD})
    assert login.status_code == 200
