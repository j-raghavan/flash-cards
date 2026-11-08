# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the management CLI (first-admin bootstrap). SQL paths need Docker."""

from __future__ import annotations

import asyncio

import pytest

from flashcards.adapters.db import Base, create_engine, create_sessionmaker
from flashcards.adapters.memory import SystemClock
from flashcards.adapters.sql import SqlInviteRepository
from flashcards.cli import create_invite, main
from flashcards.config import Settings
from flashcards.domain.invites import Invite, InviteStatus, Role


def _reset_schema(url: str) -> None:
    async def _run() -> None:
        engine = create_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_run())


def test_create_invite_requires_a_database() -> None:
    with pytest.raises(SystemExit):
        asyncio.run(create_invite(Settings(), Role.ADMIN, None))


def test_create_invite_persists_an_admin_invite(pg_url: str) -> None:
    _reset_schema(pg_url)
    token = asyncio.run(create_invite(Settings(database_url=pg_url), Role.ADMIN, None))
    assert len(token) >= 40

    async def _list() -> list[Invite]:
        engine = create_engine(pg_url)
        sessionmaker = create_sessionmaker(engine)
        async with sessionmaker() as session:
            invites = await SqlInviteRepository(session).list_all()
        await engine.dispose()
        return invites

    invites = asyncio.run(_list())
    assert len(invites) == 1
    assert invites[0].role is Role.ADMIN
    assert invites[0].status(SystemClock().now()) is InviteStatus.ACTIVE


def test_main_create_invite_prints_a_token(
    pg_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _reset_schema(pg_url)
    monkeypatch.setenv("FLASHCARDS_DATABASE_URL", pg_url)
    main(["create-invite", "--role", "member", "--email", "friend@example.com"])
    printed = capsys.readouterr().out.strip()
    assert len(printed) >= 40
