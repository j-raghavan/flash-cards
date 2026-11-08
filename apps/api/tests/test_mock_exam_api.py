# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP tests for the mock-exam routes (in-memory mode, scripted LLM)."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.api.app import create_app
from flashcards.api.container import Container
from flashcards.config import Settings
from flashcards.domain.ai import StructuredRequest, StructuredResult, TokenUsage
from flashcards.domain.invites import Role

PASSWORD = "correct-horse-battery"

EXAM_DATA: dict[str, object] = {
    "questions": [
        {"prompt": "What is 2+2?", "answer": "4", "topic": "Math"},
        {"prompt": "What is 3+3?", "answer": "6", "topic": "Math"},
    ]
}


class _ScriptedLLM:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = responses

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        return StructuredResult(
            data=self._responses.pop(0), usage=TokenUsage(1, 1), model="fake"
        )


def _app(llm: _ScriptedLLM | None = None) -> FastAPI:
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


def _add_twenty_cards(client: TestClient, deck_id: str) -> None:
    text = " ".join("{{c" + str(i) + "::ans" + str(i) + "}}" for i in range(1, 21))
    resp = client.post(
        f"/decks/{deck_id}/notes", json={"note_type": "cloze", "fields": {"text": text}}
    )
    assert resp.status_code == 201


def test_create_and_submit_a_mock_exam() -> None:
    llm = _ScriptedLLM([EXAM_DATA])
    client = _member(_app(llm))
    deck_id = _deck(client)
    _add_twenty_cards(client, deck_id)

    created = client.post(f"/decks/{deck_id}/mock-exam", json={"question_count": 2})
    assert created.status_code == 201
    body = created.json()
    # The client sees prompts and topics, never the reference answers.
    assert body["questions"][0]["prompt"] == "What is 2+2?"
    assert "reference" not in body["questions"][0]
    assert "answer" not in body["questions"][0]
    exam_id = body["id"]
    first_q = body["questions"][0]["id"]

    # The same scripted LLM backs the app, so queue the grade response for the submit call.
    llm._responses.append({"grades": [{"question_id": first_q, "score": 1.0}]})
    submitted = client.post(
        f"/mock-exams/{exam_id}/submit",
        json={"answers": [{"question_id": first_q, "answer": "4"}]},
    )
    assert submitted.status_code == 200
    result = submitted.json()
    assert result["topics"][0]["topic"] == "Math"
    scores = {qs["question_id"]: qs["score"] for qs in result["question_scores"]}
    assert scores[first_q] == 1.0


def test_mock_exam_is_locked_for_a_small_deck() -> None:
    client = _member(_app(_ScriptedLLM([EXAM_DATA])))
    deck_id = _deck(client)
    client.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "q", "back": "a"}},
    )
    assert client.post(f"/decks/{deck_id}/mock-exam", json={}).status_code == 409


def test_mock_exam_requires_auth() -> None:
    resp = TestClient(_app(_ScriptedLLM([]))).post("/decks/x/mock-exam", json={})
    assert resp.status_code == 401


def test_mock_exam_unknown_deck_is_404() -> None:
    client = _member(_app(_ScriptedLLM([EXAM_DATA])))
    assert client.post("/decks/missing/mock-exam", json={}).status_code == 404


def test_submit_unknown_exam_is_404() -> None:
    client = _member(_app(_ScriptedLLM([])))
    resp = client.post("/mock-exams/missing/submit", json={"answers": []})
    assert resp.status_code == 404


def test_mock_exam_is_owner_scoped() -> None:
    app = _app(_ScriptedLLM([EXAM_DATA]))
    owner = _member(app, "owner@example.com")
    deck_id = _deck(owner)
    _add_twenty_cards(owner, deck_id)
    exam_id = owner.post(f"/decks/{deck_id}/mock-exam", json={}).json()["id"]
    other = _member(app, "other@example.com")
    assert other.post(f"/mock-exams/{exam_id}/submit", json={"answers": []}).status_code == 404


def test_mock_exam_without_a_backend_is_503() -> None:
    client = _member(_app())  # default app wires NullLLMProvider
    deck_id = _deck(client)
    _add_twenty_cards(client, deck_id)
    assert client.post(f"/decks/{deck_id}/mock-exam", json={}).status_code == 503
