# SPDX-License-Identifier: AGPL-3.0-only
"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from flashcards import __version__
from flashcards.api.container import Container
from flashcards.api.provider import in_memory_provider, sql_provider
from flashcards.api.routers import admin, auth
from flashcards.config import Settings
from flashcards.domain.invites import Role

logger = logging.getLogger("flashcards")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    if settings.database_url:
        engine, provider = sql_provider(settings.database_url)
        container = None
    else:
        container, provider = in_memory_provider()
        engine = None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is not None:
            await engine.dispose()

    app = FastAPI(
        title="Flash-Cards API",
        version=__version__,
        lifespan=lifespan,
        # No public docs in production; enable only in development.
        docs_url="/docs" if settings.environment == "development" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.environment == "development" else None,
    )
    app.state.settings = settings
    app.state.container = container
    app.state.services_provider = provider
    app.state.bootstrap_invite_token = _maybe_bootstrap_invite(settings, container)

    app.include_router(auth.router)
    app.include_router(admin.router)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


def _maybe_bootstrap_invite(settings: Settings, container: Container | None) -> str | None:
    """Seed one admin invite at startup in dev, returning its token, else None.

    Only runs in in-memory mode; SQL mode will get a real first-admin bootstrap.
    """
    if container is None or not settings.dev_bootstrap_invite:
        return None
    if settings.environment == "production":
        logger.warning("dev_bootstrap_invite is set but ignored in production.")
        return None
    created = asyncio.run(container.invites.create(created_by="dev-bootstrap", role=Role.ADMIN))
    logger.warning(
        "DEV BOOTSTRAP (not for production): admin invite token = %s", created.raw_token
    )
    return created.raw_token


app = create_app()
