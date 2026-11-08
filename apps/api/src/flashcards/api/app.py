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
from flashcards.api.routers import admin, ai, auth, cards, decks, review
from flashcards.config import Settings
from flashcards.domain.ai import DraftNotFound
from flashcards.domain.decks import DeckCycle, DeckNotFound, InvalidDeckName
from flashcards.domain.invites import Role
from flashcards.domain.jobs import JobNotFound
from flashcards.domain.notes import CardNotFound, InvalidNote, NoteNotFound
from flashcards.domain.ports import LLMProvider
from flashcards.domain.quotas import QuotaExceeded
from flashcards.domain.sources import TooManyPages, UnsupportedMediaType

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

    async def quota_exceeded(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=429, content={"detail": "AI page quota exceeded for this period."}
        )

    async def unsupported_media(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=415, content={"detail": "Unsupported file type."})

    async def too_large(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=413, content={"detail": "The document has too many pages."}
        )

    for not_found_error in (DeckNotFound, CardNotFound, NoteNotFound, JobNotFound, DraftNotFound):
        app.add_exception_handler(not_found_error, not_found)
    app.add_exception_handler(InvalidDeckName, invalid_deck_name)
    app.add_exception_handler(InvalidNote, invalid_note)
    app.add_exception_handler(DeckCycle, conflict)
    app.add_exception_handler(QuotaExceeded, quota_exceeded)
    app.add_exception_handler(UnsupportedMediaType, unsupported_media)
    app.add_exception_handler(TooManyPages, too_large)


def create_app(settings: Settings | None = None, *, llm: LLMProvider | None = None) -> FastAPI:
    settings = settings or Settings()
    configured_llm = llm if llm is not None else _llm_from_settings(settings)

    if settings.database_url:
        engine, provider, job_queue = sql_provider(settings.database_url, llm=configured_llm)
        container = None
    else:
        container, provider, job_queue = in_memory_provider(llm=configured_llm)
        engine = None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async def process(job_id: str) -> None:
            # A fresh unit of work per job: in SQL mode this is its own session
            # and transaction, committed when generation completes.
            async for request_container in provider():
                await request_container.card_generation.run(job_id)

        job_queue.start(process)
        yield
        await job_queue.stop()
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
    app.include_router(ai.router)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


def _llm_from_settings(settings: Settings) -> LLMProvider:
    """The real Anthropic provider when a key is configured, else a refusing stub."""
    if settings.anthropic_api_key:
        from flashcards.adapters.anthropic_llm import AnthropicLLMProvider

        return AnthropicLLMProvider(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
    from flashcards.adapters.llm import NullLLMProvider

    return NullLLMProvider()


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
