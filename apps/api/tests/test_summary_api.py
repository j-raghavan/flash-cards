# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP tests for the deck-summary route (in-memory mode, fake LLM)."""

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
            data={"summary": "Deck summary.", "key_points": ["a", "b"]},
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


def _deck(client: TestClient) -> str:
    deck_id: str = client.post("/decks", json={"name": "Deck"}).json()["id"]
    return deck_id


def _add_basic(client: TestClient, deck_id: str, count: int) -> None:
    for i in range(count):
        resp = client.post(
            f"/decks/{deck_id}/notes",
            json={"note_type": "basic", "fields": {"front": f"Q{i}", "back": f"A{i}"}},
        )
        assert resp.status_code == 201


def _add_twenty_cards(client: TestClient, deck_id: str) -> None:
    # One cloze note with 20 distinct deletions yields 20 cards, crossing the default gate.
    text = " ".join("{{c" + str(i) + "::ans" + str(i) + "}}" for i in range(1, 21))
    resp = client.post(
        f"/decks/{deck_id}/notes", json={"note_type": "cloze", "fields": {"text": text}}
    )
    assert resp.status_code == 201
    assert len(resp.json()) == 20


def test_summary_unlocks_once_the_deck_is_big_enough() -> None:
    client = _member(_app(_FakeLLM()))
    deck_id = _deck(client)
    _add_twenty_cards(client, deck_id)
    resp = client.post(f"/decks/{deck_id}/summary")
    assert resp.status_code == 200
    assert resp.json() == {"summary": "Deck summary.", "key_points": ["a", "b"]}


def test_summary_is_locked_for_a_small_deck() -> None:
    client = _member(_app(_FakeLLM()))
    deck_id = _deck(client)
    _add_basic(client, deck_id, 3)
    resp = client.post(f"/decks/{deck_id}/summary")
    assert resp.status_code == 409


def test_summary_requires_auth() -> None:
    resp = TestClient(_app(_FakeLLM())).post("/decks/whatever/summary")
    assert resp.status_code == 401


def test_summary_unknown_deck_is_404() -> None:
    client = _member(_app(_FakeLLM()))
    assert client.post("/decks/missing/summary").status_code == 404


def test_summary_is_owner_scoped() -> None:
    app = _app(_FakeLLM())
    owner = _member(app, "owner@example.com")
    deck_id = _deck(owner)
    _add_twenty_cards(owner, deck_id)
    other = _member(app, "other@example.com")
    assert other.post(f"/decks/{deck_id}/summary").status_code == 404


def test_summary_without_a_backend_is_503() -> None:
    client = _member(_app())  # default app wires NullLLMProvider
    deck_id = _deck(client)
    _add_twenty_cards(client, deck_id)
    assert client.post(f"/decks/{deck_id}/summary").status_code == 503


class _BadLLM:
    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        return StructuredResult(data={"summary": ""}, usage=TokenUsage(1, 1), model="fake")


def test_summary_malformed_model_output_is_502() -> None:
    client = _member(_app(_BadLLM()))
    deck_id = _deck(client)
    _add_twenty_cards(client, deck_id)
    assert client.post(f"/decks/{deck_id}/summary").status_code == 502
