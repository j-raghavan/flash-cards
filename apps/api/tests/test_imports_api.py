# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP test for the .apkg import route (in-memory mode)."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import zipfile
from collections.abc import Callable

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.domain.invites import Role

PASSWORD = "correct-horse-battery"
SEP = "\x1f"


def _apkg_bytes() -> bytes:
    models = {"1": {"name": "Basic", "type": 0, "flds": [{"name": "Front"}, {"name": "Back"}]}}
    decks = {"100": {"name": "Spanish"}}
    notes = [(10, "g1", 1, "", f"casa{SEP}house"), (11, "g2", 1, "", f"perro{SEP}dog")]
    cards = [(10, 100, 0), (11, 100, 0)]

    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE col (models TEXT, decks TEXT)")
        conn.execute(
            "INSERT INTO col VALUES (?, ?)", (json.dumps(models), json.dumps(decks))
        )
        conn.execute(
            "CREATE TABLE notes (id INTEGER, guid TEXT, mid INTEGER, tags TEXT, flds TEXT)"
        )
        conn.executemany("INSERT INTO notes VALUES (?, ?, ?, ?, ?)", notes)
        conn.execute("CREATE TABLE cards (nid INTEGER, did INTEGER, ord INTEGER)")
        conn.executemany("INSERT INTO cards VALUES (?, ?, ?)", cards)
        conn.commit()
        conn.close()
        with open(path, "rb") as handle:
            collection = handle.read()
    finally:
        os.unlink(path)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("collection.anki21", collection)
        archive.writestr("media", "{}")
    return buffer.getvalue()


@pytest.fixture
def member(app: FastAPI, seed_invite: Callable[..., str]) -> TestClient:
    client = TestClient(app)
    token = seed_invite(role=Role.MEMBER)
    resp = client.post(
        "/auth/signup", json={"token": token, "email": "m@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 201
    return client


def _upload(client: TestClient, data: bytes) -> httpx2.Response:
    return client.post(
        "/import/apkg",
        files={"file": ("deck.apkg", data, "application/octet-stream")},
    )


def test_import_creates_a_deck_with_cards(member: TestClient) -> None:
    resp = _upload(member, _apkg_bytes())
    assert resp.status_code == 201
    report = resp.json()
    assert report == {"decks_created": 1, "notes_imported": 2, "notes_skipped": 0}

    decks = member.get("/decks").json()
    spanish = next(deck for deck in decks if deck["name"] == "Spanish")
    cards = member.get(f"/decks/{spanish['id']}/cards").json()
    assert len(cards) == 2


def test_import_requires_auth(client: TestClient) -> None:
    assert _upload(client, _apkg_bytes()).status_code == 401


def test_import_rejects_a_non_package(member: TestClient) -> None:
    assert _upload(member, b"not a zip at all").status_code == 422


def test_import_rejects_oversized_upload(
    member: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("flashcards.api.routers.imports.MAX_UPLOAD_BYTES", 10)
    assert _upload(member, _apkg_bytes()).status_code == 413
