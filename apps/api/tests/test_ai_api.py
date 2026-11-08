# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP tests for the AI generation routes (in-memory mode)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.domain.ai import DraftCardRecord
from flashcards.domain.invites import Role
from flashcards.domain.notes import NoteType

PASSWORD = "correct-horse-battery"


def _pdf(pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i} with enough text to read")
    try:
        return bytes(doc.tobytes())
    finally:
        doc.close()


def _signup(app: FastAPI, seed_invite: Callable[..., str], email: str) -> tuple[TestClient, str]:
    token = seed_invite(role=Role.MEMBER)
    client = TestClient(app)
    resp = client.post(
        "/auth/signup", json={"token": token, "email": email, "password": PASSWORD}
    )
    assert resp.status_code == 201
    return client, resp.json()["id"]


@pytest.fixture
def member(app: FastAPI, seed_invite: Callable[..., str]) -> tuple[TestClient, str]:
    return _signup(app, seed_invite, "member@example.com")


def _make_deck(client: TestClient) -> str:
    resp = client.post("/decks", json={"name": "Deck"})
    assert resp.status_code == 201
    deck_id: str = resp.json()["id"]
    return deck_id


def _upload(
    client: TestClient, deck_id: str, data: bytes, media: str = "application/pdf"
) -> httpx.Response:
    resp: httpx.Response = client.post(
        "/ai/generate",
        files={"file": ("doc", data, media)},
        data={"deck_id": deck_id, "thoroughness": "standard"},
    )
    return resp


def test_generate_enqueues_a_queued_job(member: tuple[TestClient, str]) -> None:
    client, _ = member
    deck_id = _make_deck(client)
    resp = _upload(client, deck_id, _pdf(2))
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["progress"] == 0.0
    assert body["cancel_requested"] is False


def test_generate_requires_auth(client: TestClient) -> None:
    resp = _upload(client, "deck", _pdf())
    assert resp.status_code == 401


def test_generate_rejects_unsupported_media(member: tuple[TestClient, str]) -> None:
    client, _ = member
    deck_id = _make_deck(client)
    gif = b"GIF89a" + b"\x00" * 64
    resp = _upload(client, deck_id, gif, media="image/gif")
    assert resp.status_code == 415


def test_generate_rejects_too_many_pages(member: tuple[TestClient, str]) -> None:
    client, _ = member
    deck_id = _make_deck(client)
    resp = _upload(client, deck_id, _pdf(21))
    assert resp.status_code == 413


def test_generate_rejects_over_quota(member: tuple[TestClient, str]) -> None:
    client, _ = member
    deck_id = _make_deck(client)
    # Default monthly quota is 10 pages; an 11-page document is refused.
    resp = _upload(client, deck_id, _pdf(11))
    assert resp.status_code == 429


def test_generate_rejects_oversized_upload(
    member: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = member
    deck_id = _make_deck(client)
    monkeypatch.setattr("flashcards.api.routers.ai.MAX_UPLOAD_BYTES", 10)
    resp = _upload(client, deck_id, _pdf())  # any real PDF exceeds 10 bytes
    assert resp.status_code == 413


def test_generate_on_foreign_deck_is_not_found(member: tuple[TestClient, str]) -> None:
    client, _ = member
    resp = _upload(client, "nonexistent-deck", _pdf())
    assert resp.status_code == 404


def test_job_status_and_cancel(member: tuple[TestClient, str]) -> None:
    client, _ = member
    deck_id = _make_deck(client)
    job_id = _upload(client, deck_id, _pdf()).json()["id"]

    assert client.get(f"/ai/jobs/{job_id}").json()["status"] == "queued"
    assert client.get("/ai/jobs/missing").status_code == 404

    cancelled = client.post(f"/ai/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["cancel_requested"] is True
    assert client.post("/ai/jobs/missing/cancel").status_code == 404


def test_list_drafts_empty_and_missing(member: tuple[TestClient, str]) -> None:
    client, _ = member
    deck_id = _make_deck(client)
    job_id = _upload(client, deck_id, _pdf()).json()["id"]
    assert client.get(f"/ai/jobs/{job_id}/drafts").json() == []
    assert client.get("/ai/jobs/missing/drafts").status_code == 404


def test_approve_promotes_drafts_into_the_deck(
    app: FastAPI, member: tuple[TestClient, str]
) -> None:
    client, owner_id = member
    deck_id = _make_deck(client)
    job_id = _upload(client, deck_id, _pdf()).json()["id"]

    # No worker yet, so seed the drafts this job would have produced.
    container = app.state.container
    for i in range(2):
        asyncio.run(
            container.draft_review._drafts.add(
                DraftCardRecord(
                    id=f"draft-{i}",
                    job_id=job_id,
                    owner_id=owner_id,
                    note_type=NoteType.BASIC,
                    fields=(("front", f"Q{i}"), ("back", f"A{i}")),
                    source_page=1,
                    created_at=container.clock.now(),
                )
            )
        )

    assert len(client.get(f"/ai/jobs/{job_id}/drafts").json()) == 2
    approved = client.post(
        f"/ai/jobs/{job_id}/approve", json={"draft_ids": ["draft-0", "draft-1"]}
    )
    assert approved.status_code == 201
    assert len(approved.json()) == 2  # two basic drafts -> two cards
    # The cards are now in the deck, and the drafts are consumed.
    assert len(client.get(f"/decks/{deck_id}/cards").json()) == 2
    assert client.get(f"/ai/jobs/{job_id}/drafts").json() == []


def test_approve_validation_and_unknown_draft(member: tuple[TestClient, str]) -> None:
    client, _ = member
    deck_id = _make_deck(client)
    job_id = _upload(client, deck_id, _pdf()).json()["id"]
    # Empty selection is rejected by the request schema.
    assert client.post(f"/ai/jobs/{job_id}/approve", json={"draft_ids": []}).status_code == 422
    # An unknown draft id is not found.
    bad = client.post(f"/ai/jobs/{job_id}/approve", json={"draft_ids": ["nope"]})
    assert bad.status_code == 404
