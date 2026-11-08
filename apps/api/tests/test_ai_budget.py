# SPDX-License-Identifier: AGPL-3.0-only
"""The daily synchronous AI-call budget and the concurrency slot on AI routes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.adapters.memory import InMemoryQuotaRepository
from flashcards.api.app import create_app
from flashcards.api.container import Container
from flashcards.application.ai_budget import AiCallBudget
from flashcards.config import Settings
from flashcards.domain.ai import StructuredRequest, StructuredResult, TokenUsage
from flashcards.domain.invites import Role
from flashcards.domain.quotas import AiCallLimitReached, ai_call_period

PASSWORD = "correct-horse-battery"
NOW = datetime(2026, 11, 8, 23, 59, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def test_the_daily_period_never_collides_with_a_monthly_one() -> None:
    assert ai_call_period(NOW) == "calls:2026-11-08"


async def test_the_budget_allows_up_to_the_limit_then_refuses() -> None:
    budget = AiCallBudget(InMemoryQuotaRepository(limit=2), _Clock())
    await budget.charge("alice")
    await budget.charge("alice")
    with pytest.raises(AiCallLimitReached):
        await budget.charge("alice")
    await budget.charge("bob")  # per owner


class _FakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        self.calls += 1
        return StructuredResult(
            data={"verdict": "correct", "score": 1, "explanation": "ok"},
            usage=TokenUsage(1, 1),
            model="fake",
        )


def _member(app: FastAPI) -> TestClient:
    container: Container = app.state.container
    token = asyncio.run(container.invites.create(created_by="seed", role=Role.MEMBER)).raw_token
    client = TestClient(app)
    resp = client.post(
        "/auth/signup", json={"token": token, "email": "m@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 201
    return client


def _card(client: TestClient) -> str:
    deck_id = client.post("/decks", json={"name": "Deck"}).json()["id"]
    resp = client.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "2+2?", "back": "4"}},
    )
    card_id: str = resp.json()[0]["id"]
    return card_id


def test_the_daily_limit_returns_429_without_calling_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Wire a one-call daily budget through the real container.
    monkeypatch.setattr("flashcards.api.container.DEFAULT_DAILY_AI_CALLS", 1)
    llm = _FakeLLM()
    app = create_app(Settings(environment="test", session_cookie_secure=False), llm=llm)
    client = _member(app)
    card_id = _card(client)

    assert client.post(f"/cards/{card_id}/evaluate", json={"answer": "4"}).status_code == 200
    second = client.post(f"/cards/{card_id}/evaluate", json={"answer": "4"})

    assert second.status_code == 429
    assert second.json()["detail"] == "Daily AI request limit reached."
    assert llm.calls == 1


def test_a_full_slot_pool_is_503_and_never_opens_a_db_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        Settings(environment="test", session_cookie_secure=False, max_concurrent_ai_calls=1),
        llm=_FakeLLM(),
    )
    client = _member(app)
    card_id = _card(client)

    opened: list[str] = []
    real_provider = app.state.services_provider

    async def recording_provider() -> AsyncIterator[Container]:
        opened.append("session")
        async for container in real_provider():
            yield container

    monkeypatch.setattr("flashcards.api.deps.AI_SLOT_WAIT_SECONDS", 0.01)
    app.state.services_provider = recording_provider
    app.state.ai_slots = asyncio.Semaphore(0)  # every slot is taken

    resp = client.post(f"/cards/{card_id}/evaluate", json={"answer": "4"})

    assert resp.status_code == 503
    assert resp.json()["detail"] == "AI is busy right now; try again shortly."
    # The slot is acquired before the DB session, so a waiting request holds none.
    assert opened == []


def test_the_slot_is_released_after_each_request() -> None:
    app = create_app(
        Settings(environment="test", session_cookie_secure=False, max_concurrent_ai_calls=1),
        llm=_FakeLLM(),
    )
    client = _member(app)
    card_id = _card(client)
    for _ in range(3):  # would hang and 503 if the single slot leaked
        assert client.post(f"/cards/{card_id}/evaluate", json={"answer": "4"}).status_code == 200


class _ClosingLLM(_FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def test_the_provider_is_closed_on_shutdown() -> None:
    llm = _ClosingLLM()
    app = create_app(Settings(environment="test", session_cookie_secure=False), llm=llm)
    with TestClient(app):  # runs the lifespan
        assert llm.closed is False
    assert llm.closed is True
