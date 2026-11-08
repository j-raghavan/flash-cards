# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP tests for deck share links (in-memory mode)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.api.app import create_app
from flashcards.api.container import Container
from flashcards.config import Settings
from flashcards.domain.invites import Role

PASSWORD = "correct-horse-battery"


@pytest.fixture
def app() -> FastAPI:
    return create_app(Settings(environment="test", session_cookie_secure=False))


def _member(app: FastAPI, email: str) -> TestClient:
    container: Container = app.state.container
    token = asyncio.run(container.invites.create(created_by="seed", role=Role.MEMBER)).raw_token
    client = TestClient(app)
    resp = client.post(
        "/auth/signup", json={"token": token, "email": email, "password": PASSWORD}
    )
    assert resp.status_code == 201
    return client


def _deck_with_note(client: TestClient) -> str:
    deck_id: str = client.post("/decks", json={"name": "Spanish"}).json()["id"]
    resp = client.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "hola", "back": "hello"}},
    )
    assert resp.status_code == 201
    return deck_id


def test_share_preview_clone_flow(app: FastAPI) -> None:
    owner = _member(app, "owner@example.com")
    deck_id = _deck_with_note(owner)

    created = owner.post(f"/decks/{deck_id}/shares", json={"allow_clone": True})
    assert created.status_code == 201
    body = created.json()
    token, share_id = body["token"], body["id"]
    assert body["allow_clone"] is True and body["revoked_at"] is None

    listed = owner.get(f"/decks/{deck_id}/shares").json()
    assert [s["id"] for s in listed] == [share_id]
    assert "token" not in listed[0]  # the token is only shown once

    recipient = _member(app, "friend@example.com")
    preview = recipient.get(f"/shared/{token}")
    assert preview.status_code == 200
    assert preview.json()["name"] == "Spanish"
    assert preview.json()["notes"][0]["fields"] == {"front": "hola", "back": "hello"}
    assert "owner" not in str(preview.json())

    clone = recipient.post(f"/shared/{token}/clone")
    assert clone.status_code == 201
    assert clone.json()["name"] == "Spanish"
    clone_cards = recipient.get(f"/decks/{clone.json()['id']}/cards").json()
    assert len(clone_cards) == 1


def test_toggle_and_revoke(app: FastAPI) -> None:
    owner = _member(app, "owner@example.com")
    deck_id = _deck_with_note(owner)
    body = owner.post(f"/decks/{deck_id}/shares", json={}).json()
    token, share_id = body["token"], body["id"]
    recipient = _member(app, "friend@example.com")

    assert recipient.post(f"/shared/{token}/clone").status_code == 403
    patched = owner.patch(f"/shares/{share_id}", json={"allow_clone": True})
    assert patched.status_code == 200 and patched.json()["allow_clone"] is True

    revoked = owner.post(f"/shares/{share_id}/revoke")
    assert revoked.status_code == 200 and revoked.json()["revoked_at"] is not None
    assert recipient.get(f"/shared/{token}").status_code == 404
    assert recipient.post(f"/shared/{token}/clone").status_code == 404


def test_only_the_owner_manages_links(app: FastAPI) -> None:
    owner = _member(app, "owner@example.com")
    deck_id = _deck_with_note(owner)
    share_id = owner.post(f"/decks/{deck_id}/shares", json={}).json()["id"]
    other = _member(app, "other@example.com")
    assert other.post(f"/decks/{deck_id}/shares", json={}).status_code == 404
    assert other.get(f"/decks/{deck_id}/shares").status_code == 404
    assert other.patch(f"/shares/{share_id}", json={"allow_clone": True}).status_code == 404
    assert other.post(f"/shares/{share_id}/revoke").status_code == 404


def test_shared_routes_require_an_account(app: FastAPI) -> None:
    owner = _member(app, "owner@example.com")
    deck_id = _deck_with_note(owner)
    token = owner.post(f"/decks/{deck_id}/shares", json={"allow_clone": True}).json()["token"]
    anon = TestClient(app)
    assert anon.get(f"/shared/{token}").status_code == 401
    assert anon.post(f"/shared/{token}/clone").status_code == 401


def test_unknown_token_is_404(app: FastAPI) -> None:
    client = _member(app, "someone@example.com")
    assert client.get("/shared/nope").status_code == 404


def test_oversized_clone_is_413(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("flashcards.application.sharing_service.MAX_CLONE_NOTES", 0)
    owner = _member(app, "owner@example.com")
    deck_id = _deck_with_note(owner)
    token = owner.post(f"/decks/{deck_id}/shares", json={"allow_clone": True}).json()["token"]
    assert owner.post(f"/shared/{token}/clone").status_code == 413
