# SPDX-License-Identifier: AGPL-3.0-only
"""The Alembic migration builds the same schema the models expect (needs Docker)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
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
    "totp_credentials",
    "recovery_codes",
    "passkeys",
    "two_factor_challenges",
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


def _execute(url: str, *statements: str) -> None:
    async def _run() -> None:
        engine = create_engine(url)
        async with engine.begin() as conn:
            for statement in statements:
                await conn.exec_driver_sql(statement)
        await engine.dispose()

    asyncio.run(_run())


def _schedule_rows(url: str) -> tuple[list[tuple[str, str]], list[str]]:
    """The (user_id, card_id) of every schedule, and the table's primary key columns."""

    async def _run() -> tuple[list[tuple[str, str]], list[str]]:
        engine = create_engine(url)
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql(
                "SELECT user_id, card_id FROM card_schedule ORDER BY user_id, card_id"
            )
            rows = [(str(row["user_id"]), str(row["card_id"])) for row in result.mappings()]
            pk: list[str] = await conn.run_sync(
                lambda c: list(inspect(c).get_pk_constraint("card_schedule")["constrained_columns"])
            )
        await engine.dispose()
        return rows, pk

    return asyncio.run(_run())


def test_0019_keeps_every_schedule_as_its_owners(pg_url: str) -> None:
    _reset(pg_url)
    cfg = _alembic_config(pg_url)
    command.upgrade(cfg, "0018")
    # A schedule as 0018 stores it: keyed by the card alone.
    _execute(
        pg_url,
        "INSERT INTO decks (id, owner_id, name, parent_id, created_at) "
        "VALUES ('d1', 'admin', 'Deck', NULL, now())",
        "INSERT INTO notes (id, owner_id, deck_id, note_type, fields, created_at) "
        "VALUES ('n1', 'admin', 'd1', 'basic', '{\"front\": \"Q\", \"back\": \"A\"}', now())",
        "INSERT INTO cards (id, owner_id, deck_id, note_id, ordinal, created_at) "
        "VALUES ('c1', 'admin', 'd1', 'n1', 0, now())",
        "INSERT INTO card_schedule (card_id, state, step, stability, difficulty, due_at, "
        "last_reviewed_at) VALUES ('c1', 'review', NULL, 3.0, 5.0, now(), now())",
    )

    command.upgrade(cfg, "0019")
    assert _schedule_rows(pg_url) == ([("admin", "c1")], ["user_id", "card_id"])
    # A member's own progress on the same card now fits next to the owner's.
    _execute(
        pg_url,
        "INSERT INTO card_schedule (user_id, card_id, state, step, stability, difficulty, "
        "due_at, last_reviewed_at) VALUES ('member', 'c1', 'new', NULL, NULL, NULL, now(), NULL)",
    )

    # The old key cannot hold the member's row, so the downgrade refuses to drop it.
    with pytest.raises(RuntimeError, match="Restore the backup"):
        command.downgrade(cfg, "0018")
    _execute(pg_url, "DELETE FROM card_schedule WHERE user_id = 'member'")
    command.downgrade(cfg, "0018")

    async def _remaining() -> list[str]:
        engine = create_engine(pg_url)
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql("SELECT card_id FROM card_schedule")
            cards = [str(card) for card in result.scalars()]
        await engine.dispose()
        return cards

    assert asyncio.run(_remaining()) == ["c1"]
    command.downgrade(cfg, "base")


def test_0020_gives_every_existing_account_the_default_look(pg_url: str) -> None:
    _reset(pg_url)
    cfg = _alembic_config(pg_url)
    command.upgrade(cfg, "0019")
    _execute(
        pg_url,
        "INSERT INTO users (id, email, password_hash, role, created_at) "
        "VALUES ('u1', 'old@example.com', 'h', 'member', now())",
    )

    command.upgrade(cfg, "0020")

    async def _look() -> list[tuple[str, str]]:
        engine = create_engine(pg_url)
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql("SELECT theme, accent FROM users")
            looks = [(str(row["theme"]), str(row["accent"])) for row in result.mappings()]
        await engine.dispose()
        return looks

    assert asyncio.run(_look()) == [("system", "teal")]
    command.downgrade(cfg, "0019")
    command.downgrade(cfg, "base")


def test_migrations_match_the_models(pg_url: str) -> None:
    """After upgrading to head, autogenerate finds nothing to change: no schema drift."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy.engine import Connection

    _reset(pg_url)
    command.upgrade(_alembic_config(pg_url), "head")

    def _diff(conn: Connection) -> list[object]:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        return list(compare_metadata(context, Base.metadata))

    async def _run() -> list[object]:
        engine = create_engine(pg_url)
        async with engine.connect() as conn:
            found: list[object] = await conn.run_sync(_diff)
        await engine.dispose()
        return found

    differences = asyncio.run(_run())
    command.downgrade(_alembic_config(pg_url), "base")
    assert differences == []
