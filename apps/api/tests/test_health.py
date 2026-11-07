# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from fastapi.testclient import TestClient

from flashcards.api.app import create_app
from flashcards.config import Settings


def test_healthz_returns_ok() -> None:
    client = TestClient(create_app(Settings(environment="test")))
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_docs_are_hidden_outside_development() -> None:
    client = TestClient(create_app(Settings(environment="production")))
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_docs_are_available_in_development() -> None:
    client = TestClient(create_app(Settings(environment="development")))
    assert client.get("/docs").status_code == 200


def test_invite_only_is_on_by_default() -> None:
    assert Settings().invite_only is True


def test_lifespan_runs_for_in_memory_app() -> None:
    # Entering the context runs startup/shutdown; in-memory mode has no engine to dispose.
    with TestClient(create_app(Settings(environment="test"))) as client:
        assert client.get("/healthz").status_code == 200
