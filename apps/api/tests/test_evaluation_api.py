# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP tests for the AI-evaluation route (in-memory mode, fake LLM)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.api.app import create_app
from flashcards.config import Settings
from flashcards.domain.ai import (
    LlmError,
    StructuredRequest,
    StructuredResult,
    TokenUsage,
)
from flashcards.domain.invites import Role

PASSWORD = "correct-horse-battery"


class _FakeLLM:
    def __init__(self, data: dict[str, object] | None = None, error: Exception | None = None):
        self._data = data or {"verdict": "partial", "score": 0.5, "explanation": "Close."}
        self._error = error

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        if self._error is not None:
            raise self._error
        return StructuredResult(data=self._data, usage=TokenUsage(1, 1), model="fake")


def _app(llm: _FakeLLM) -> FastAPI:
    return create_app(Settings(environment="test", session_cookie_secure=False), llm=llm)


def _member(app: FastAPI) -> TestClient:
    import asyncio

    from flashcards.api.container import Container

    container: Container = app.state.container
    token = asyncio.run(container.invites.create(created_by="seed", role=Role.MEMBER)).raw_token
    client = TestClient(app)
    resp = client.post(
        "/auth/signup", json={"token": token, "email": "m@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 201
    return client


def _make_card(client: TestClient) -> str:
    deck_id = client.post("/decks", json={"name": "Deck"}).json()["id"]
    resp = client.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "2+2?", "back": "4"}},
    )
    assert resp.status_code == 201
    card_id: str = resp.json()[0]["id"]
    return card_id


def test_evaluate_returns_a_grade() -> None:
    client = _member(_app(_FakeLLM({"verdict": "correct", "score": 1, "explanation": "Yes."})))
    card_id = _make_card(client)
    resp = client.post(f"/cards/{card_id}/evaluate", json={"answer": "4"})
    assert resp.status_code == 200
    assert resp.json() == {"verdict": "correct", "score": 1.0, "explanation": "Yes."}


def test_evaluate_requires_auth() -> None:
    app = _app(_FakeLLM())
    resp = TestClient(app).post("/cards/whatever/evaluate", json={"answer": "4"})
    assert resp.status_code == 401


def test_evaluate_unknown_card_is_404() -> None:
    client = _member(_app(_FakeLLM()))
    resp = client.post("/cards/missing/evaluate", json={"answer": "4"})
    assert resp.status_code == 404


def test_evaluate_is_owner_scoped() -> None:
    app = _app(_FakeLLM())
    owner = _member(app)
    card_id = _make_card(owner)

    container = app.state.container
    import asyncio

    token = asyncio.run(
        container.invites.create(created_by="seed", role=Role.MEMBER)
    ).raw_token
    other = TestClient(app)
    other.post(
        "/auth/signup", json={"token": token, "email": "other@example.com", "password": PASSWORD}
    )
    assert other.post(f"/cards/{card_id}/evaluate", json={"answer": "4"}).status_code == 404


@pytest.mark.parametrize("answer", ["", "x" * 20_001])
def test_evaluate_rejects_bad_answer_length(answer: str) -> None:
    client = _member(_app(_FakeLLM()))
    card_id = _make_card(client)
    assert client.post(f"/cards/{card_id}/evaluate", json={"answer": answer}).status_code == 422


def test_evaluate_provider_failure_is_503() -> None:
    client = _member(_app(_FakeLLM(error=LlmError("model down"))))
    card_id = _make_card(client)
    assert client.post(f"/cards/{card_id}/evaluate", json={"answer": "4"}).status_code == 503


def test_evaluate_not_configured_is_503() -> None:
    # The default app wires NullLLMProvider, which refuses with an LlmError.
    app = create_app(Settings(environment="test", session_cookie_secure=False))
    client = _member(app)
    card_id = _make_card(client)
    assert client.post(f"/cards/{card_id}/evaluate", json={"answer": "4"}).status_code == 503
