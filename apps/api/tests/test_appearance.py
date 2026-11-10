# SPDX-License-Identifier: AGPL-3.0-only
"""Each user's chosen look: light or dark, and an accent palette."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from flashcards.adapters.db import Base, create_engine, create_sessionmaker
from flashcards.adapters.memory import InMemoryUserRepository
from flashcards.adapters.sql import SqlUserRepository
from flashcards.application.appearance_service import AppearanceService
from flashcards.domain.appearance import Accent, Appearance, Theme
from flashcards.domain.invites import Role
from flashcards.domain.users import User, UserNotFound

PASSWORD = "correct-horse-battery"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _user(user_id: str = "u1") -> User:
    return User(
        id=user_id,
        email=f"{user_id}@example.com",
        password_hash="h",
        role=Role.MEMBER,
        created_at=NOW,
    )


def _sign_up(app: FastAPI, seed_invite: Callable[..., str], email: str) -> TestClient:
    client = TestClient(app)
    resp = client.post(
        "/auth/signup",
        json={"token": seed_invite(role=Role.MEMBER), "email": email, "password": PASSWORD},
    )
    assert resp.status_code == 201
    return client


def test_a_new_account_follows_the_device_with_the_teal_palette() -> None:
    assert _user().appearance == Appearance(theme=Theme.SYSTEM, accent=Accent.TEAL)


def test_the_look_is_saved_on_the_account_and_sent_with_the_user(
    app: FastAPI, seed_invite: Callable[..., str]
) -> None:
    ada = _sign_up(app, seed_invite, "ada@example.com")
    me = ada.get("/auth/me").json()
    assert (me["theme"], me["accent"]) == ("system", "teal")

    # Each part changes on its own; the other keeps its value.
    resp = ada.patch("/account/appearance", json={"theme": "dark"})
    assert resp.status_code == 200
    assert resp.json() == {"theme": "dark", "accent": "teal"}
    assert ada.patch("/account/appearance", json={"accent": "rose"}).json() == {
        "theme": "dark",
        "accent": "rose",
    }
    assert ada.patch("/account/appearance", json={}).json() == {"theme": "dark", "accent": "rose"}

    # It follows the user to a new sign-in, and is theirs alone.
    other = TestClient(app)
    login = other.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    assert (login.json()["theme"], login.json()["accent"]) == ("dark", "rose")
    bob = _sign_up(app, seed_invite, "bob@example.com")
    assert (bob.get("/auth/me").json()["theme"], bob.get("/auth/me").json()["accent"]) == (
        "system",
        "teal",
    )


@pytest.mark.parametrize(
    "body", [{"theme": "sepia"}, {"accent": "purple"}, {"theme": None, "accent": 3}]
)
def test_an_unknown_choice_is_refused(
    app: FastAPI, seed_invite: Callable[..., str], body: dict[str, object]
) -> None:
    ada = _sign_up(app, seed_invite, "ada@example.com")
    assert ada.patch("/account/appearance", json=body).status_code == 422
    assert ada.get("/auth/me").json()["theme"] == "system"


def test_changing_the_look_needs_a_session(client: TestClient) -> None:
    assert client.patch("/account/appearance", json={"theme": "light"}).status_code == 401


async def test_the_service_refuses_an_unknown_user() -> None:
    with pytest.raises(UserNotFound):
        await AppearanceService(InMemoryUserRepository()).update(user_id="nobody", theme=Theme.DARK)


async def test_the_service_keeps_what_is_not_given() -> None:
    users = InMemoryUserRepository()
    await users.add(_user())
    service = AppearanceService(users)
    assert await service.update(user_id="u1", accent=Accent.AMBER) == Appearance(
        theme=Theme.SYSTEM, accent=Accent.AMBER
    )
    assert await service.update(user_id="u1", theme=Theme.LIGHT) == Appearance(
        theme=Theme.LIGHT, accent=Accent.AMBER
    )
    stored = await users.get("u1")
    assert stored is not None
    assert stored.appearance == Appearance(theme=Theme.LIGHT, accent=Accent.AMBER)


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def test_the_look_round_trips_through_postgres(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlUserRepository(s).add(_user())
    async with sm() as s:
        fresh = await SqlUserRepository(s).get("u1")
    assert fresh is not None
    assert fresh.appearance == Appearance()

    async with sm() as s, s.begin():
        await AppearanceService(SqlUserRepository(s)).update(
            user_id="u1", theme=Theme.DARK, accent=Accent.INDIGO
        )
    async with sm() as s:
        changed = await SqlUserRepository(s).get("u1")
    assert changed is not None
    assert changed.appearance == Appearance(theme=Theme.DARK, accent=Accent.INDIGO)
