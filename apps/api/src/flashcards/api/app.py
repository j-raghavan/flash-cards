# SPDX-License-Identifier: AGPL-3.0-only
"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

from flashcards import __version__
from flashcards.api.container import Container, build_container
from flashcards.api.routers import admin, auth
from flashcards.config import Settings
from flashcards.domain.invites import Role

logger = logging.getLogger("flashcards")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(
        title="Flash-Cards API",
        version=__version__,
        # No public docs in production; enable only in development.
        docs_url="/docs" if settings.environment == "development" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.environment == "development" else None,
    )
    app.state.settings = settings
    container = build_container()
    app.state.container = container
    app.state.bootstrap_invite_token = _maybe_bootstrap_invite(settings, container)

    app.include_router(auth.router)
    app.include_router(admin.router)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


def _maybe_bootstrap_invite(settings: Settings, container: Container) -> str | None:
    """Seed one admin invite at startup in dev, returning its token, else None."""
    if not settings.dev_bootstrap_invite:
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
