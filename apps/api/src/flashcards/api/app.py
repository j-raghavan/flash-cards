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
from flashcards.api.routers import (
    account,
    admin,
    ai,
    auth,
    cards,
    decks,
    goals,
    imports,
    media,
    mock_exams,
    review,
)
from flashcards.config import Settings
from flashcards.domain.ai import DraftNotFound, LlmError
from flashcards.domain.anki import InvalidPackage, UnsupportedPackage
from flashcards.domain.decks import DeckCycle, DeckNotFound, InvalidDeckName
from flashcards.domain.goals import GoalNotFound, InvalidGoal
from flashcards.domain.invites import Role
from flashcards.domain.jobs import JobNotFound
from flashcards.domain.media import MediaNotFound, MediaTooLarge, UnsupportedMedia
from flashcards.domain.mock_exam import ExamLocked, ExamNotFound
from flashcards.domain.notes import CardNotFound, InvalidNote, NoteNotFound
from flashcards.domain.ports import LLMProvider
from flashcards.domain.quotas import QuotaExceeded
from flashcards.domain.sources import TooManyPages, UnsupportedMediaType
from flashcards.domain.summary import SummaryLocked
from flashcards.domain.transcripts import (
    InvalidTranscript,
    InvalidVideoUrl,
    TranscriptUnavailable,
)

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

    async def invalid_goal(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    async def bad_package(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "The .apkg could not be imported."})

    async def media_too_large(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=413, content={"detail": "The media file is too large."})

    async def feature_locked(_request: Request, exc: Exception) -> JSONResponse:
        # A card-threshold gate (summary or mock exam) is not met yet.
        detail = str(exc) if isinstance(exc, SummaryLocked | ExamLocked) else "Feature locked."
        return JSONResponse(status_code=409, content={"detail": detail})

    async def invalid_video_url(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "Not a YouTube video link."})

    async def no_transcript(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "No transcript is available for this video. Paste the transcript instead."
            },
        )

    async def invalid_transcript(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    async def ai_unavailable(_request: Request, _exc: Exception) -> JSONResponse:
        # A synchronous AI call failed or is not configured (the async generation
        # worker captures its own errors onto the job and never reaches here).
        return JSONResponse(
            status_code=503, content={"detail": "AI is unavailable right now."}
        )

    not_found_errors = (
        DeckNotFound,
        CardNotFound,
        NoteNotFound,
        JobNotFound,
        DraftNotFound,
        GoalNotFound,
        MediaNotFound,
        ExamNotFound,
    )
    for not_found_error in not_found_errors:
        app.add_exception_handler(not_found_error, not_found)
    app.add_exception_handler(InvalidDeckName, invalid_deck_name)
    app.add_exception_handler(InvalidNote, invalid_note)
    app.add_exception_handler(InvalidGoal, invalid_goal)
    app.add_exception_handler(InvalidPackage, bad_package)
    app.add_exception_handler(UnsupportedPackage, bad_package)
    app.add_exception_handler(UnsupportedMedia, unsupported_media)
    app.add_exception_handler(MediaTooLarge, media_too_large)
    app.add_exception_handler(DeckCycle, conflict)
    app.add_exception_handler(QuotaExceeded, quota_exceeded)
    app.add_exception_handler(UnsupportedMediaType, unsupported_media)
    app.add_exception_handler(TooManyPages, too_large)
    app.add_exception_handler(InvalidVideoUrl, invalid_video_url)
    app.add_exception_handler(TranscriptUnavailable, no_transcript)
    app.add_exception_handler(InvalidTranscript, invalid_transcript)
    app.add_exception_handler(SummaryLocked, feature_locked)
    app.add_exception_handler(ExamLocked, feature_locked)
    # Catches InvalidEvaluation, InvalidSummary and InvalidExam too (all LlmError).
    app.add_exception_handler(LlmError, ai_unavailable)


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
    app.include_router(goals.router)
    app.include_router(imports.router)
    app.include_router(account.router)
    app.include_router(media.router)
    app.include_router(mock_exams.router)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


def _llm_from_settings(settings: Settings) -> LLMProvider:
    """Select the AI backend from settings, else a refusing stub (ADR 0006)."""
    from flashcards.adapters.llm import NullLLMProvider

    if settings.llm_provider == "openai_compatible":
        if settings.openai_base_url:
            from flashcards.adapters.openai_llm import OpenAICompatibleLLMProvider

            return OpenAICompatibleLLMProvider(
                base_url=settings.openai_base_url,
                model=settings.openai_model,
                api_key=settings.openai_api_key,
            )
        return NullLLMProvider()
    if settings.anthropic_api_key:
        from flashcards.adapters.anthropic_llm import AnthropicLLMProvider

        return AnthropicLLMProvider(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
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
