# SPDX-License-Identifier: AGPL-3.0-only
"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from flashcards import __version__
from flashcards.api.container import Container
from flashcards.api.provider import in_memory_provider, sql_provider
from flashcards.api.routers import admin, auth, cards, decks, review
from flashcards.config import Settings
from flashcards.domain.decks import DeckCycle, DeckNotFound, InvalidDeckName
from flashcards.domain.invites import Role
from flashcards.domain.notes import CardNotFound, InvalidNote, NoteNotFound

logger = logging.getLogger("flashcards")


def _register_error_handlers(app: FastAPI) -> None:
    """Map domain errors to HTTP. Not-found stays generic; invalid input is actionable."""

    def not_found(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Not found."})

    async def invalid_note(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    async def invalid_deck_name(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "Invalid deck name."})

    async def conflict(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "A deck cannot be moved under itself or a descendant."},
        )

    for not_found_error in (DeckNotFound, CardNotFound, NoteNotFound):
        app.add_exception_handler(not_found_error, not_found)
    app.add_exception_handler(InvalidDeckName, invalid_deck_name)
    app.add_exception_handler(InvalidNote, invalid_note)
    app.add_exception_handler(DeckCycle, conflict)


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

    _register_error_handlers(app)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(decks.router)
    app.include_router(cards.router)
    app.include_router(review.router)

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
