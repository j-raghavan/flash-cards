# SPDX-License-Identifier: AGPL-3.0-only
"""Integration tests for the SQLAlchemy adapters against a real Postgres.

These require Docker: a throwaway Postgres container is started per test session.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from flashcards.adapters.db import (
    Base,
    create_engine,
    create_sessionmaker,
)
from flashcards.adapters.memory import SystemClock
from flashcards.adapters.passwords import Argon2PasswordHasher
from flashcards.adapters.sql import (
    SqlInviteRepository,
    SqlSessionRepository,
    SqlUserRepository,
)
from flashcards.api.app import create_app
from flashcards.api.container import build_services
from flashcards.application.invite_service import InviteService
from flashcards.config import Settings
from flashcards.domain.invites import InviteStatus, Role
from flashcards.domain.sessions import Session
from flashcards.domain.users import EmailAlreadyRegistered, User

PASSWORD = "correct-horse-battery"


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[object]:
    eng = create_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


def _now() -> object:
    return SystemClock().now()


async def test_user_add_get_and_duplicate(engine: object) -> None:
    sm = create_sessionmaker(engine)  # type: ignore[arg-type]
    user = User(
        id="u1",
        email="person@example.com",
        password_hash="hash",
        role=Role.MEMBER,
        created_at=SystemClock().now(),
    )
    async with sm() as s, s.begin():
        await SqlUserRepository(s).add(user)
    async with sm() as s:
        repo = SqlUserRepository(s)
        assert (await repo.get("u1")) is not None
        found = await repo.get_by_email("person@example.com")
        assert found is not None and found.id == "u1"
        assert (await repo.get("missing")) is None
        assert (await repo.get_by_email("none@example.com")) is None
    # Duplicate email is rejected.
    async with sm() as s, s.begin():
        with pytest.raises(EmailAlreadyRegistered):
            await SqlUserRepository(s).add(
                User(
                    id="u2",
                    email="person@example.com",
                    password_hash="hash",
                    role=Role.MEMBER,
                    created_at=SystemClock().now(),
                )
            )


async def test_invite_lifecycle(engine: object) -> None:
    sm = create_sessionmaker(engine)  # type: ignore[arg-type]
    async with sm() as s, s.begin():
        created = await InviteService(SqlInviteRepository(s), SystemClock()).create(
            created_by="admin", role=Role.ADMIN
        )
    invite_id = created.invite.id
    async with sm() as s:
        repo = SqlInviteRepository(s)
        assert (await repo.get(invite_id)) is not None
        assert (await repo.get_by_token_hash(created.invite.token_hash)) is not None
        assert [i.id for i in await repo.list_all()] == [invite_id]
    # Single-use redemption.
    async with sm() as s, s.begin():
        repo = SqlInviteRepository(s)
        assert await repo.try_redeem(invite_id, SystemClock().now()) is True
    async with sm() as s, s.begin():
        repo = SqlInviteRepository(s)
        assert await repo.try_redeem(invite_id, SystemClock().now()) is False


async def test_revoke_blocks_redeem(engine: object) -> None:
    sm = create_sessionmaker(engine)  # type: ignore[arg-type]
    async with sm() as s, s.begin():
        created = await InviteService(SqlInviteRepository(s), SystemClock()).create(
            created_by="admin"
        )
    async with sm() as s, s.begin():
        repo = SqlInviteRepository(s)
        assert await repo.try_revoke(created.invite.id, SystemClock().now()) is True
    async with sm() as s, s.begin():
        repo = SqlInviteRepository(s)
        assert await repo.try_revoke(created.invite.id, SystemClock().now()) is False
        assert await repo.try_redeem(created.invite.id, SystemClock().now()) is False


async def test_session_lifecycle(engine: object) -> None:
    sm = create_sessionmaker(engine)  # type: ignore[arg-type]
    now = SystemClock().now()
    sess = Session(
        id="s1",
        user_id="u1",
        token_hash="th1",
        created_at=now,
        expires_at=now,
    )
    async with sm() as s, s.begin():
        await SqlSessionRepository(s).add(sess)
    async with sm() as s:
        repo = SqlSessionRepository(s)
        assert (await repo.get_by_token_hash("th1")) is not None
        assert (await repo.get_by_token_hash("nope")) is None
        assert [x.id for x in await repo.list_for_user("u1")] == ["s1"]
    async with sm() as s, s.begin():
        assert await SqlSessionRepository(s).revoke("s1", SystemClock().now()) is True
    async with sm() as s, s.begin():
        assert await SqlSessionRepository(s).revoke("s1", SystemClock().now()) is False
        assert await SqlSessionRepository(s).revoke("missing", SystemClock().now()) is False


async def test_registration_commits_atomically(engine: object) -> None:
    sm = create_sessionmaker(engine)  # type: ignore[arg-type]
    hasher = Argon2PasswordHasher()
    async with sm() as s, s.begin():
        created = await InviteService(SqlInviteRepository(s), SystemClock()).create(
            created_by="admin", role=Role.MEMBER
        )
    async with sm() as s, s.begin():
        services = build_services(
            SystemClock(),
            SqlInviteRepository(s),
            SqlUserRepository(s),
            SqlSessionRepository(s),
            hasher,
        )
        await services.registration.register(
            raw_token=created.raw_token, email="new@example.com", password=PASSWORD
        )
    async with sm() as s:
        assert (await SqlUserRepository(s).get_by_email("new@example.com")) is not None
        invite = await SqlInviteRepository(s).get(created.invite.id)
        assert invite is not None
        assert invite.status(SystemClock().now()) is InviteStatus.REDEEMED


async def test_unit_of_work_rolls_back_on_error(engine: object) -> None:
    sm = create_sessionmaker(engine)  # type: ignore[arg-type]
    async with sm() as s, s.begin():
        created = await InviteService(SqlInviteRepository(s), SystemClock()).create(
            created_by="admin"
        )
    with pytest.raises(RuntimeError):
        async with sm() as s, s.begin():
            assert await SqlInviteRepository(s).try_redeem(
                created.invite.id, SystemClock().now()
            ) is True
            raise RuntimeError("boom after redeem")
    # The redemption was rolled back with the failed transaction.
    async with sm() as s:
        invite = await SqlInviteRepository(s).get(created.invite.id)
        assert invite is not None
        assert invite.status(SystemClock().now()) is InviteStatus.ACTIVE


def test_end_to_end_signup_over_http_on_postgres(pg_url: str) -> None:
    # Seed a committed admin invite, then drive the real SQL-backed app over HTTP.
    # Schema + seed run in their own engine/loop so nothing is shared with the
    # app's engine, which binds to the TestClient's event loop.
    async def seed() -> str:
        eng = create_engine(pg_url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        sm = create_sessionmaker(eng)
        async with sm() as s, s.begin():
            created = await InviteService(SqlInviteRepository(s), SystemClock()).create(
                created_by="seed", role=Role.ADMIN
            )
        await eng.dispose()
        return created.raw_token

    token = asyncio.run(seed())
    app = create_app(
        Settings(database_url=pg_url, environment="test", session_cookie_secure=False)
    )
    with TestClient(app) as client:
        signup = client.post(
            "/auth/signup",
            json={"token": token, "email": "admin@example.com", "password": PASSWORD},
        )
        assert signup.status_code == 201
        assert signup.json()["role"] == "admin"
        me = client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "admin@example.com"
