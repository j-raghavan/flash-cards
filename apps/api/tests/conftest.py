# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterator

# Testcontainers' Ryuk reaper fails to start under some Docker Desktop setups;
# the container context managers clean up their own containers, so disable it.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
# Bound the readiness wait so a wedged daemon fails fast instead of hanging.
os.environ.setdefault("TESTCONTAINERS_CONNECTION_TIMEOUT", "60")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from testcontainers.postgres import PostgresContainer

from flashcards.api.app import create_app
from flashcards.config import Settings
from flashcards.domain.invites import Role

PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """A throwaway Postgres for the SQL integration tests (needs Docker)."""
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


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
        created = asyncio.run(
            app.state.container.invites.create(created_by="seed", role=role, email=email)
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
