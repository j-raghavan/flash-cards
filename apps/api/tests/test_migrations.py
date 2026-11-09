# SPDX-License-Identifier: AGPL-3.0-only
"""The Alembic migration builds the same schema the models expect (needs Docker)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from flashcards.adapters.db import Base, create_engine, create_sessionmaker
from flashcards.adapters.memory import SystemClock
from flashcards.adapters.sql import SqlUserRepository
from flashcards.domain.invites import Role
from flashcards.domain.users import User

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
APP_TABLES = {
    "invites",
    "users",
    "sessions",
    "decks",
    "notes",
    "cards",
    "card_schedule",
    "review_logs",
    "ai_jobs",
    "draft_cards",
    "ai_blobs",
    "ai_quota",
    "goals",
    "media_assets",
    "mock_exams",
    "document_passages",
    "share_links",
    "audit_events",
    "login_throttle",
    "user_tokens",
    "note_types",
    "session_activity",
    "ai_usage",
    "instance_quotas",
    "user_quotas",
    "ai_spend_holds",
    "study_settings",
    "review_last_answers",
    "plan_baselines",
}


def _alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _reset(url: str) -> None:
    async def _run() -> None:
        engine = create_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
        await engine.dispose()

    asyncio.run(_run())


def _table_names(url: str) -> set[str]:
    async def _run() -> set[str]:
        engine = create_engine(url)
        async with engine.connect() as conn:
            names: set[str] = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        await engine.dispose()
        return names

    return asyncio.run(_run())


def _user_round_trips(url: str) -> bool:
    async def _run() -> bool:
        engine = create_engine(url)
        sessionmaker = create_sessionmaker(engine)
        async with sessionmaker() as session, session.begin():
            await SqlUserRepository(session).add(
                User(
                    id="m1",
                    email="migrated@example.com",
                    password_hash="h",
                    role=Role.MEMBER,
                    created_at=SystemClock().now(),
                )
            )
        async with sessionmaker() as session:
            found = await SqlUserRepository(session).get_by_email("migrated@example.com")
        await engine.dispose()
        return found is not None

    return asyncio.run(_run())


def test_migration_upgrade_then_downgrade(pg_url: str) -> None:
    _reset(pg_url)
    cfg = _alembic_config(pg_url)

    command.upgrade(cfg, "head")
    assert APP_TABLES.issubset(_table_names(pg_url))
    # The migrated columns match the ORM models, so a repository round-trips.
    assert _user_round_trips(pg_url) is True

    command.downgrade(cfg, "base")
    assert APP_TABLES.isdisjoint(_table_names(pg_url))
