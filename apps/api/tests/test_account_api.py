# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP test for the account data export (in-memory mode)."""

from __future__ import annotations

from collections.abc import Callable

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.domain.invites import Role

PASSWORD = "correct-horse-battery"


def _png() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=16, height=16)
    try:
        return bytes(page.get_pixmap().tobytes("png"))
    finally:
        doc.close()


@pytest.fixture
def member(app: FastAPI, seed_invite: Callable[..., str]) -> TestClient:
    client = TestClient(app)
    token = seed_invite(role=Role.MEMBER)
    resp = client.post(
        "/auth/signup", json={"token": token, "email": "me@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 201
    return client


def test_export_returns_all_the_users_data(member: TestClient) -> None:
    deck_id = member.post("/decks", json={"name": "Spanish"}).json()["id"]
    card_id = member.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "casa", "back": "house"}},
    ).json()[0]["id"]
    assert member.post(f"/cards/{card_id}/review", json={"rating": 3}).status_code == 200
    member.post(
        "/goals",
        json={
            "name": "Final",
            "deck_ids": [deck_id],
            "exam_date": "2030-01-01T00:00:00Z",
            "desired_grade": 0.5,
        },
    )
    member.post("/media", files={"file": ("x", _png(), "image/png")})

    resp = member.get("/account/export")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "me@example.com"
    assert len(body["decks"]) == 1
    assert len(body["notes"]) == 1
    assert len(body["cards"]) == 1
    assert len(body["schedules"]) == 1  # the reviewed card has a schedule
    assert len(body["review_logs"]) == 1
    assert len(body["goals"]) == 1
    assert len(body["media"]) == 1 and body["media"][0]["content_type"] == "image/png"
    assert body["mock_exams"] == []


def test_export_requires_auth(client: TestClient) -> None:
    assert client.get("/account/export").status_code == 401


def test_delete_account_revokes_access(member: TestClient) -> None:
    member.post("/decks", json={"name": "Spanish"})
    assert member.delete("/account").status_code == 204
    # The account is gone, so the session no longer authenticates.
    assert member.get("/decks").status_code == 401


def test_delete_requires_auth(client: TestClient) -> None:
    assert client.delete("/account").status_code == 401
