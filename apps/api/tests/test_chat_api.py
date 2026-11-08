# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP tests for document chat (in-memory mode, fake LLM)."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.api.app import create_app
from flashcards.api.container import Container
from flashcards.config import Settings
from flashcards.domain.ai import StructuredRequest, StructuredResult, TokenUsage
from flashcards.domain.invites import Role
from flashcards.domain.ports import LLMProvider

PASSWORD = "correct-horse-battery"


class _FakeLLM:
    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        return StructuredResult(
            data={"answer": "In the mitochondria.", "citations": [1]},
            usage=TokenUsage(1, 1),
            model="fake",
        )


def _app(llm: LLMProvider | None = None) -> FastAPI:
    settings = Settings(environment="test", session_cookie_secure=False)
    return create_app(settings, llm=llm) if llm is not None else create_app(settings)


def _member(app: FastAPI, email: str = "m@example.com") -> TestClient:
    container: Container = app.state.container
    token = asyncio.run(container.invites.create(created_by="seed", role=Role.MEMBER)).raw_token
    client = TestClient(app)
    resp = client.post(
        "/auth/signup", json={"token": token, "email": email, "password": PASSWORD}
    )
    assert resp.status_code == 201
    return client


def _deck_with_source(client: TestClient) -> str:
    deck_id: str = client.post("/decks", json={"name": "Bio"}).json()["id"]
    resp = client.post(
        "/ai/generate/transcript",
        json={"deck_id": deck_id, "transcript": "Mitochondria make ATP for the cell."},
    )
    assert resp.status_code == 202
    return deck_id


def test_chat_answers_with_a_page_citation() -> None:
    client = _member(_app(_FakeLLM()))
    deck_id = _deck_with_source(client)
    resp = client.post(f"/decks/{deck_id}/chat", json={"question": "Where is ATP made?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "In the mitochondria."
    assert len(body["citations"]) == 1 and body["citations"][0]["page"] == 1


def test_chat_without_sources_is_409() -> None:
    client = _member(_app(_FakeLLM()))
    deck_id = client.post("/decks", json={"name": "Empty"}).json()["id"]
    assert client.post(f"/decks/{deck_id}/chat", json={"question": "ATP?"}).status_code == 409


def test_chat_unknown_deck_is_404() -> None:
    client = _member(_app(_FakeLLM()))
    assert client.post("/decks/missing/chat", json={"question": "ATP?"}).status_code == 404


def test_chat_is_owner_scoped() -> None:
    app = _app(_FakeLLM())
    owner = _member(app, "owner@example.com")
    deck_id = _deck_with_source(owner)
    other = _member(app, "other@example.com")
    assert other.post(f"/decks/{deck_id}/chat", json={"question": "ATP?"}).status_code == 404


def test_chat_rejects_an_empty_question() -> None:
    client = _member(_app(_FakeLLM()))
    deck_id = _deck_with_source(client)
    assert client.post(f"/decks/{deck_id}/chat", json={"question": ""}).status_code == 422


def test_chat_requires_auth() -> None:
    resp = TestClient(_app(_FakeLLM())).post("/decks/x/chat", json={"question": "q"})
    assert resp.status_code == 401


def test_chat_without_a_backend_is_503() -> None:
    client = _member(_app())  # default app wires NullLLMProvider
    deck_id = _deck_with_source(client)
    assert client.post(f"/decks/{deck_id}/chat", json={"question": "ATP?"}).status_code == 503


class _BadLLM:
    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        return StructuredResult(data={"answer": ""}, usage=TokenUsage(1, 1), model="fake")


def test_chat_malformed_model_output_is_502() -> None:
    client = _member(_app(_BadLLM()))
    deck_id = _deck_with_source(client)
    assert client.post(f"/decks/{deck_id}/chat", json={"question": "ATP?"}).status_code == 502


def test_chat_rejects_an_overlong_question() -> None:
    client = _member(_app(_FakeLLM()))
    deck_id = _deck_with_source(client)
    resp = client.post(f"/decks/{deck_id}/chat", json={"question": "q" * 2_001})
    assert resp.status_code == 422
