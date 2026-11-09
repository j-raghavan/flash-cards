# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterator
from functools import partialmethod

# Testcontainers' Ryuk reaper fails to start under some Docker Desktop setups;
# the container context managers clean up their own containers, so disable it.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
# Bound the readiness wait so a wedged daemon fails fast instead of hanging.
os.environ.setdefault("TESTCONTAINERS_CONNECTION_TIMEOUT", "60")
# Every TestClient comes from one address, and many tests sign in repeatedly, so
# the per-address rate limit is off by default here; tests/test_rate_limit.py turns
# it on explicitly (ADR 0019).
os.environ.setdefault("FLASHCARDS_RATE_LIMIT_ENABLED", "false")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from testcontainers.postgres import PostgresContainer

from flashcards.api.app import create_app
from flashcards.api.container import Container
from flashcards.config import Settings
from flashcards.domain.invites import Role

PASSWORD = "correct-horse-battery"


# The header the web client sends on every request. The CSRF middleware accepts an
# unsafe request that carries no Origin, Sec-Fetch-Site or Referer only with it
# (ADR 0017), so every test client sends it, as a non-browser client must.
WEB_CLIENT_HEADERS = {"X-Requested-With": "fetch"}


@pytest.fixture(autouse=True)
def _clients_identify_as_the_web_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        TestClient, "__init__", partialmethod(TestClient.__init__, headers=WEB_CLIENT_HEADERS)
    )


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """A throwaway Postgres for the SQL integration tests (needs Docker)."""
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield str(pg.get_connection_url())


@pytest.fixture
def app() -> FastAPI:
    # session_cookie_secure=False so the TestClient's HTTP transport echoes the cookie back.
    return create_app(Settings(environment="test", session_cookie_secure=False))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def seed_invite(app: FastAPI) -> Callable[..., str]:
    def _seed(role: Role = Role.MEMBER, email: str | None = None) -> str:
        container: Container = app.state.container
        created = asyncio.run(
            container.invites.create(created_by="seed", role=role, email=email)
        )
        return created.raw_token

    return _seed


@pytest.fixture
def admin_client(app: FastAPI, seed_invite: Callable[..., str]) -> TestClient:
    client = TestClient(app)
    token = seed_invite(role=Role.ADMIN)
    response = client.post(
        "/auth/signup",
        json={"token": token, "email": "admin@example.com", "password": PASSWORD},
    )
    assert response.status_code == 201
    return client
