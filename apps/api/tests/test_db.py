# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from flashcards.adapters.db import normalize_async_url


def test_url_with_driver_is_unchanged() -> None:
    assert normalize_async_url("postgresql+asyncpg://h/db") == "postgresql+asyncpg://h/db"


def test_postgresql_scheme_is_coerced_to_asyncpg() -> None:
    assert (
        normalize_async_url("postgresql://u:p@h:5432/db") == "postgresql+asyncpg://u:p@h:5432/db"
    )


def test_postgres_scheme_is_coerced_to_asyncpg() -> None:
    assert normalize_async_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


def test_unknown_scheme_is_left_alone() -> None:
    assert normalize_async_url("sqlite+aiosqlite:///x.db") == "sqlite+aiosqlite:///x.db"
