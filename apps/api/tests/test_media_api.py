# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP tests for the media upload/serve routes (in-memory mode)."""

from __future__ import annotations

from collections.abc import Callable

import httpx2
import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.domain.invites import Role

PASSWORD = "correct-horse-battery"


def _png() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=32, height=32)
    try:
        return bytes(page.get_pixmap().tobytes("png"))
    finally:
        doc.close()


def _signup(app: FastAPI, seed_invite: Callable[..., str], email: str) -> TestClient:
    client = TestClient(app)
    token = seed_invite(role=Role.MEMBER)
    resp = client.post(
        "/auth/signup", json={"token": token, "email": email, "password": PASSWORD}
    )
    assert resp.status_code == 201
    return client


@pytest.fixture
def member(app: FastAPI, seed_invite: Callable[..., str]) -> TestClient:
    return _signup(app, seed_invite, "a@example.com")


def _upload(client: TestClient, data: bytes, media: str = "image/png") -> httpx2.Response:
    return client.post("/media", files={"file": ("x", data, media)})


def test_upload_then_fetch_round_trips(member: TestClient) -> None:
    data = _png()
    created = _upload(member, data)
    assert created.status_code == 201
    body = created.json()
    assert body["content_type"] == "image/png"

    fetched = member.get(f"/media/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.content == data
    assert fetched.headers["content-type"] == "image/png"


def test_upload_requires_auth(client: TestClient) -> None:
    assert _upload(client, _png()).status_code == 401


def test_unsupported_media_is_rejected(member: TestClient) -> None:
    pdf = b"%PDF-1.4 not really a media file"
    assert _upload(member, pdf, media="application/pdf").status_code == 415


def test_oversized_media_is_rejected(
    app: FastAPI, member: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.state.container.media, "_max_bytes", 10)
    assert _upload(member, _png()).status_code == 413


def test_fetch_is_owner_scoped(
    app: FastAPI, seed_invite: Callable[..., str], member: TestClient
) -> None:
    media_id = _upload(member, _png()).json()["id"]
    other = _signup(app, seed_invite, "b@example.com")
    assert other.get(f"/media/{media_id}").status_code == 404
    assert member.get("/media/missing").status_code == 404
