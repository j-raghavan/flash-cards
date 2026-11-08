# SPDX-License-Identifier: AGPL-3.0-only
"""Service providers: how a Container is produced per request.

In-memory mode yields one shared Container. SQL mode yields a Container bound to
a fresh AsyncSession inside a transaction, so each request is one unit of work
that commits on success and rolls back on any error.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Callable

from sqlalchemy.ext.asyncio import AsyncEngine

from flashcards.adapters.db import create_engine, create_sessionmaker
from flashcards.adapters.memory import SystemClock
from flashcards.adapters.passwords import Argon2PasswordHasher
from flashcards.adapters.sql import (
    SqlInviteRepository,
    SqlSessionRepository,
    SqlUserRepository,
)
from flashcards.api.container import Container, build_container, build_services

ServicesProvider = Callable[[], AsyncIterator[Container]]

TOKEN_BYTES = 32


def in_memory_provider() -> tuple[Container, ServicesProvider]:
    container = build_container()

    async def provider() -> AsyncIterator[Container]:
        yield container

    return container, provider


def sql_provider(database_url: str) -> tuple[AsyncEngine, ServicesProvider]:
    engine = create_engine(database_url)
    sessionmaker = create_sessionmaker(engine)
    clock = SystemClock()
    hasher = Argon2PasswordHasher()
    dummy_hash = hasher.hash(secrets.token_urlsafe(TOKEN_BYTES))

    async def provider() -> AsyncIterator[Container]:
        async with sessionmaker() as session, session.begin():
            yield build_services(
                clock,
                SqlInviteRepository(session),
                SqlUserRepository(session),
                SqlSessionRepository(session),
                hasher,
                dummy_hash,
            )

    return engine, provider
