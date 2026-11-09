# SPDX-License-Identifier: AGPL-3.0-only
"""Postgres tests for two-factor sign-in (F-AUTH-3; needs Docker)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from soft_authenticator import SoftAuthenticator
from sqlalchemy.ext.asyncio import AsyncEngine
from two_factor_contract import NOW, check_repository

from flashcards.adapters.db import Base, create_engine, create_sessionmaker
from flashcards.adapters.memory import SystemClock
from flashcards.adapters.sql import SqlInviteRepository, SqlUserRepository
from flashcards.adapters.sql_two_factor import SqlTwoFactorRepository
from flashcards.adapters.two_factor import FernetSecretBox
from flashcards.api.app import create_app
from flashcards.application.invite_service import InviteService
from flashcards.cli import main, rotate_totp_key
from flashcards.config import Settings
from flashcards.domain.invites import Role
from flashcards.domain.two_factor import TotpCredential
from flashcards.domain.users import User

PASSWORD = "correct-horse-battery"
EMAIL = "person@example.com"


def _user(user_id: str) -> User:
    return User(
        id=user_id,
        email=f"{user_id}@example.com",
        password_hash="h",
        role=Role.MEMBER,
        created_at=NOW,
    )


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def test_sql_repository_contract(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        users = SqlUserRepository(s)
        await users.add(_user("u1"))
        await users.add(_user("u2"))
        await check_repository(SqlTwoFactorRepository(s), "u1", "u2")


async def test_deleting_a_user_cascades(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlUserRepository(s).add(_user("u1"))
        await SqlTwoFactorRepository(s).save_totp(
            TotpCredential(user_id="u1", secret_ciphertext="x", created_at=NOW)
        )
    async with sm() as s, s.begin():
        await SqlUserRepository(s).delete("u1")
    async with sm() as s:
        assert await SqlTwoFactorRepository(s).get_totp("u1") is None


def _seed(pg_url: str) -> str:
    async def seed() -> str:
        eng = create_engine(pg_url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        sm = create_sessionmaker(eng)
        async with sm() as s, s.begin():
            created = await InviteService(SqlInviteRepository(s), SystemClock()).create(
                created_by="seed", role=Role.MEMBER
            )
        await eng.dispose()
        return created.raw_token

    return asyncio.run(seed())


def _code(secret: str, offset: int = 0) -> str:
    return pyotp.TOTP(secret).at(datetime.now(UTC), counter_offset=offset)


def test_two_factor_over_http_on_postgres(
    pg_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    token = _seed(pg_url)
    old_key = Fernet.generate_key().decode()
    settings = Settings(
        database_url=pg_url,
        environment="test",
        session_cookie_secure=False,
        totp_encryption_keys=old_key,
    )
    device = SoftAuthenticator()
    with TestClient(create_app(settings)) as client:
        signup = client.post(
            "/auth/signup", json={"token": token, "email": EMAIL, "password": PASSWORD}
        )
        assert signup.status_code == 201
        setup = client.post("/auth/two-factor/totp", json={"password": PASSWORD}).json()
        secret = setup["secret"]
        confirmed = client.post("/auth/two-factor/totp/confirm", json={"code": _code(secret)})
        codes = confirmed.json()["recovery_codes"]
        started = client.post(
            "/auth/two-factor/passkeys/options", json={"password": PASSWORD}
        ).json()
        added = client.post(
            "/auth/two-factor/passkeys",
            json={"token": started["token"], "credential": device.register(started["options"])},
        )
        assert added.status_code == 201 and added.json()["recovery_codes"] is None
        client.post("/auth/logout")

        login = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert login.status_code == 202
        pending = login.json()["token"]
        # A recovery code signs in once; the session is issued only then.
        used = client.post("/auth/login/second-factor", json={"token": pending, "code": codes[0]})
        assert used.status_code == 200 and client.get("/auth/me").status_code == 200
        client.post("/auth/logout")

        pending = client.post(
            "/auth/login", json={"email": EMAIL, "password": PASSWORD}
        ).json()["token"]
        options = client.post("/auth/login/passkey/options", json={"token": pending}).json()
        signed = client.post(
            "/auth/login/passkey",
            json={"token": pending, "credential": device.assert_(options["options"])},
        )
        assert signed.status_code == 200
        client.post("/auth/logout")

        # Wrong codes are committed: the attempts survive each request's transaction.
        pending = client.post(
            "/auth/login", json={"email": EMAIL, "password": PASSWORD}
        ).json()["token"]
        for _ in range(4):
            wrong = client.post(
                "/auth/login/second-factor", json={"token": pending, "code": "000000"}
            )
            assert wrong.status_code == 401
        locked = client.post(
            "/auth/login/second-factor", json={"token": pending, "code": _code(secret, 1)}
        )
        assert locked.status_code == 429

    # Rotation: put a new key in front, re-encrypt, then the old key can go.
    new_key = Fernet.generate_key().decode()
    monkeypatch.setenv("FLASHCARDS_DATABASE_URL", pg_url)
    monkeypatch.setenv("FLASHCARDS_TOTP_ENCRYPTION_KEYS", f"{new_key},{old_key}")
    main(["rotate-totp-key"])
    assert "Re-encrypted 1 authenticator secret(s)." in capsys.readouterr().out

    async def stored_secret() -> str:
        eng = create_engine(pg_url)
        sm = create_sessionmaker(eng)
        async with sm() as s:
            credentials = await SqlTwoFactorRepository(s).list_totp()
        await eng.dispose()
        return FernetSecretBox([new_key]).open(credentials[0].secret_ciphertext)

    assert asyncio.run(stored_secret()) == secret


def test_rotation_needs_a_database_and_a_key() -> None:
    with pytest.raises(SystemExit):
        asyncio.run(rotate_totp_key(Settings(totp_encryption_keys=Fernet.generate_key().decode())))
    with pytest.raises(SystemExit):
        asyncio.run(rotate_totp_key(Settings(database_url="postgresql+asyncpg://x/y")))


def test_generate_key_prints_a_usable_key(capsys: pytest.CaptureFixture[str]) -> None:
    main(["generate-totp-key"])
    key = capsys.readouterr().out.strip()
    assert FernetSecretBox([key]).open(FernetSecretBox([key]).seal("s")) == "s"
