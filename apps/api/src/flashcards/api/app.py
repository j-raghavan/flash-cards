# SPDX-License-Identifier: AGPL-3.0-only
"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Protocol, runtime_checkable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from flashcards import __version__
from flashcards.adapters.legal_pages import load_legal_pages
from flashcards.adapters.llm import NullLLMProvider
from flashcards.adapters.transcripts import NullTranscriptProvider
from flashcards.adapters.two_factor import FernetSecretBox, parse_keys
from flashcards.api.body_limit import BodySizeLimitMiddleware
from flashcards.api.container import Container
from flashcards.api.csrf import CsrfOriginMiddleware
from flashcards.api.job_events import StreamSlots
from flashcards.api.provider import ServicesProvider, in_memory_provider, sql_provider
from flashcards.api.rate_limit import RateLimitMiddleware, default_rules
from flashcards.api.routers import (
    account,
    admin,
    ai,
    auth,
    cards,
    chat,
    decks,
    goals,
    imports,
    legal,
    media,
    mock_exams,
    note_types,
    progress,
    review,
    sharing,
    two_factor,
)
from flashcards.config import Settings, fake_llm_refusal
from flashcards.domain.ai import DraftNotFound, InvalidModelOutput, LlmError
from flashcards.domain.anki import InvalidPackage, UnsupportedPackage
from flashcards.domain.chat import NoChatSources
from flashcards.domain.decks import DeckCycle, DeckNotFound, InvalidDeckName
from flashcards.domain.disclosure import AiDisclosure, ai_disclosure
from flashcards.domain.goals import GoalNotFound, InvalidGoal
from flashcards.domain.invites import Role
from flashcards.domain.jobs import IllegalJobTransition, JobNotFound
from flashcards.domain.media import MediaNotFound, MediaTooLarge, UnsupportedMedia
from flashcards.domain.mock_exam import ExamClosed, ExamLocked, ExamNotFound
from flashcards.domain.note_types import (
    InvalidNoteType,
    NoteTypeInUse,
    NoteTypeNotFound,
    TooManyNoteTypes,
)
from flashcards.domain.notes import (
    CardNotFound,
    InvalidBury,
    InvalidFlag,
    InvalidNote,
    NoteNotFound,
)
from flashcards.domain.ports import LLMProvider, Mailer, SecretBox, TranscriptProvider
from flashcards.domain.progress import InvalidRange, InvalidStudySettings
from flashcards.domain.quotas import (
    MICRO_USD_PER_USD,
    AiCallLimitReached,
    InstanceQuotas,
    InvalidQuota,
    QuotaExceeded,
)
from flashcards.domain.scheduling import ReviewNotFound, UndoRejected
from flashcards.domain.sharing import (
    CloneLimitReached,
    CloneNotAllowed,
    CloneTooLarge,
    ShareNotFound,
)
from flashcards.domain.sources import TooManyPages, UnreadableDocument, UnsupportedMediaType
from flashcards.domain.summary import SummaryLocked
from flashcards.domain.tags import InvalidTags
from flashcards.domain.transcripts import (
    InvalidTranscript,
    InvalidVideoUrl,
    TranscriptUnavailable,
    parse_language_preference,
)
from flashcards.domain.usage import AiMetering, SpendCapReached, TokenPrices
from flashcards.domain.users import AdminRequired, AdminSelfAction, LastActiveAdmin, UserNotFound

logger = logging.getLogger("flashcards")


def _register_error_handlers(app: FastAPI) -> None:
    """Map domain errors to HTTP. Not-found stays generic; invalid input is actionable."""

    def not_found(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Not found."})

    async def invalid_request(_request: Request, exc: Exception) -> JSONResponse:
        # FastAPI's default 422 echoes each offending value back ("input"), which can
        # be megabytes long or a password. Keep where and why, never the value.
        errors = exc.errors() if isinstance(exc, RequestValidationError) else []
        detail = [
            {"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")}
            for e in errors
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    async def invalid_note(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    async def note_type_in_use(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": "Notes still use this note type."}
        )

    async def too_many_note_types(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": "The note type limit has been reached."}
        )

    async def invalid_deck_name(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "Invalid deck name."})

    async def conflict(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "A deck cannot be moved under itself or a descendant."},
        )

    async def quota_exceeded(_request: Request, exc: Exception) -> JSONResponse:
        # The message is written for the user: the document's pages and what is left.
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    async def unsupported_media(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=415, content={"detail": "Unsupported file type."})

    async def too_large(_request: Request, exc: Exception) -> JSONResponse:
        # The message states the document's page count and the limit.
        return JSONResponse(status_code=413, content={"detail": str(exc)})

    async def unreadable_document(_request: Request, exc: Exception) -> JSONResponse:
        logger.warning("unreadable upload: %s", exc)
        return JSONResponse(status_code=422, content={"detail": "The document could not be read."})

    async def invalid_goal(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    async def invalid_progress(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    async def undo_rejected(_request: Request, exc: Exception) -> JSONResponse:
        # The answer exists but cannot be taken back; the message says why.
        return JSONResponse(status_code=409, content={"detail": str(exc)})

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

    async def ai_call_limit(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=429, content={"detail": "Daily AI request limit reached."}
        )

    async def clone_not_allowed(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=403, content={"detail": "Cloning is not allowed for this link."}
        )

    async def clone_too_large(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=413, content={"detail": "The shared deck is too large to clone."}
        )

    async def clone_limit(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=429, content={"detail": "Daily clone limit reached."})

    async def admin_self_action(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "Admins cannot do this to their own account."},
        )

    async def last_admin(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": "At least one active admin must remain."}
        )

    async def admin_required(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": "Admin only."})

    async def exam_closed(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    async def no_chat_sources(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "This deck has no source documents with text to chat with."},
        )

    async def ai_unavailable(_request: Request, exc: Exception) -> JSONResponse:
        # A synchronous AI call failed or is not configured (the async generation
        # worker captures its own errors onto the job and never reaches here).
        logger.warning("AI call failed: %s", exc, exc_info=exc)
        return JSONResponse(
            status_code=503, content={"detail": "AI is unavailable right now."}
        )

    async def ai_bad_output(_request: Request, exc: Exception) -> JSONResponse:
        # The provider answered but the output failed the domain's schema.
        logger.warning("AI returned unusable output: %s", exc, exc_info=exc)
        return JSONResponse(
            status_code=502,
            content={"detail": "The AI returned an unusable answer; try again."},
        )

    async def spend_cap(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": "This instance's monthly AI budget is used up."},
        )

    async def invalid_quota(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    async def job_conflict(_request: Request, _exc: Exception) -> JSONResponse:
        # e.g. retrying a job that has not failed or has no attempts left.
        return JSONResponse(
            status_code=409, content={"detail": "The job cannot do that in its current state."}
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
        ShareNotFound,
        UserNotFound,
        NoteTypeNotFound,
        ReviewNotFound,
    )
    for not_found_error in not_found_errors:
        app.add_exception_handler(not_found_error, not_found)
    app.add_exception_handler(IllegalJobTransition, job_conflict)
    app.add_exception_handler(InvalidDeckName, invalid_deck_name)
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.add_exception_handler(InvalidNote, invalid_note)
    app.add_exception_handler(InvalidNoteType, invalid_note)
    app.add_exception_handler(NoteTypeInUse, note_type_in_use)
    app.add_exception_handler(TooManyNoteTypes, too_many_note_types)
    app.add_exception_handler(InvalidTags, invalid_note)
    app.add_exception_handler(InvalidFlag, invalid_note)
    app.add_exception_handler(InvalidBury, invalid_note)
    app.add_exception_handler(InvalidGoal, invalid_goal)
    app.add_exception_handler(InvalidStudySettings, invalid_progress)
    app.add_exception_handler(InvalidRange, invalid_progress)
    app.add_exception_handler(UndoRejected, undo_rejected)
    app.add_exception_handler(InvalidPackage, bad_package)
    app.add_exception_handler(UnsupportedPackage, bad_package)
    app.add_exception_handler(UnsupportedMedia, unsupported_media)
    app.add_exception_handler(MediaTooLarge, media_too_large)
    app.add_exception_handler(DeckCycle, conflict)
    app.add_exception_handler(QuotaExceeded, quota_exceeded)
    app.add_exception_handler(UnsupportedMediaType, unsupported_media)
    app.add_exception_handler(TooManyPages, too_large)
    app.add_exception_handler(UnreadableDocument, unreadable_document)
    app.add_exception_handler(InvalidVideoUrl, invalid_video_url)
    app.add_exception_handler(TranscriptUnavailable, no_transcript)
    app.add_exception_handler(InvalidTranscript, invalid_transcript)
    app.add_exception_handler(NoChatSources, no_chat_sources)
    app.add_exception_handler(ExamClosed, exam_closed)
    app.add_exception_handler(AdminSelfAction, admin_self_action)
    app.add_exception_handler(LastActiveAdmin, last_admin)
    app.add_exception_handler(AdminRequired, admin_required)
    app.add_exception_handler(CloneNotAllowed, clone_not_allowed)
    app.add_exception_handler(CloneTooLarge, clone_too_large)
    app.add_exception_handler(CloneLimitReached, clone_limit)
    app.add_exception_handler(AiCallLimitReached, ai_call_limit)
    app.add_exception_handler(SpendCapReached, spend_cap)
    app.add_exception_handler(InvalidQuota, invalid_quota)
    app.add_exception_handler(SummaryLocked, feature_locked)
    app.add_exception_handler(ExamLocked, feature_locked)
    # The more specific handler wins: malformed output is 502, anything else 503.
    app.add_exception_handler(InvalidModelOutput, ai_bad_output)
    app.add_exception_handler(LlmError, ai_unavailable)


def _secret_box_from_settings(settings: Settings, *, persistent: bool) -> SecretBox | None:
    """The key that encrypts authenticator secrets (ADR 0018).

    Without one, a persistent (SQL) instance offers no authenticator apps, since
    secrets stored now could not be read after a restart with a different key;
    in-memory mode lets build_container use a throwaway key.
    """
    keys = parse_keys(settings.totp_encryption_keys or "")
    if keys:
        return FernetSecretBox(keys)
    if persistent:
        logger.warning(
            "FLASHCARDS_TOTP_ENCRYPTION_KEYS is not set: authenticator apps are unavailable."
        )
    return None


def create_app(settings: Settings | None = None, *, llm: LLMProvider | None = None) -> FastAPI:
    settings = settings or Settings()
    configured_llm = llm if llm is not None else _llm_from_settings(settings)

    ai_enabled = not isinstance(configured_llm, NullLLMProvider)
    metering = _metering_from_settings(settings, ai_enabled=ai_enabled)
    mailer = _mailer_from_settings(settings)
    transcripts = _transcripts_from_settings(settings)
    legal_pages = load_legal_pages(settings.legal_dir)  # raises on an unusable page
    _warn_on_local_links(settings)
    if settings.database_url:
        engine, provider, job_queue = sql_provider(
            settings.database_url,
            llm=configured_llm,
            mailer=mailer,
            public_base_url=settings.public_base_url,
            metering=metering,
            require_verified_sign_in=not settings.invite_only,
            max_upload_pages=settings.max_upload_pages,
            transcripts=transcripts,
            secret_box=_secret_box_from_settings(settings, persistent=True),
        )
        container = None
    else:
        container, provider, job_queue = in_memory_provider(
            llm=configured_llm,
            mailer=mailer,
            public_base_url=settings.public_base_url,
            metering=metering,
            require_verified_sign_in=not settings.invite_only,
            max_upload_pages=settings.max_upload_pages,
            transcripts=transcripts,
            secret_box=_secret_box_from_settings(settings, persistent=False),
        )
        engine = None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async def process(job_id: str) -> None:
            # A fresh unit of work per job: in SQL mode this is its own session
            # and transaction, committed when generation completes.
            async for request_container in provider():
                await request_container.card_generation.run(job_id)

        await _recover_jobs(provider)
        job_queue.start(process)
        sweeper = asyncio.create_task(_sweep_jobs(provider))
        yield
        sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper
        # Stopping the worker hands its running job back as QUEUED (ADR 0017).
        await job_queue.stop()
        if isinstance(configured_llm, Closeable):
            await configured_llm.aclose()
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
    app.state.event_stream_slots = StreamSlots()
    app.state.ai_disclosure = _disclosure_from_settings(settings, ai_enabled=ai_enabled)
    app.state.legal_pages = legal_pages
    if not settings.invite_only:
        logger.warning("INVITE_ONLY is off: anyone can create an account at /auth/register.")
    app.state.bootstrap_invite_token = _maybe_bootstrap_invite(settings, container)

    # Refuses unsafe requests that do not come from a trusted origin (ADR 0017).
    app.add_middleware(CsrfOriginMiddleware, public_base_url=settings.public_base_url)
    if settings.rate_limit_enabled:
        # Runs before the CSRF check, so a refused burst costs nothing (ADR 0019).
        app.add_middleware(
            RateLimitMiddleware,
            rules=default_rules(
                auth_per_minute=settings.rate_limit_auth_per_minute,
                shared_per_minute=settings.rate_limit_shared_per_minute,
            ),
            trusted_proxies=settings.trusted_proxy_count,
        )
    # Added last, so it runs first: no body is parsed or spooled past the cap.
    app.add_middleware(BodySizeLimitMiddleware)
    _register_error_handlers(app)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(decks.router)
    app.include_router(cards.router)
    app.include_router(review.router)
    app.include_router(ai.router)
    app.include_router(goals.router)
    app.include_router(progress.router)
    app.include_router(imports.router)
    app.include_router(account.router)
    app.include_router(media.router)
    app.include_router(mock_exams.router)
    app.include_router(chat.router)
    app.include_router(sharing.router)
    app.include_router(note_types.router)
    app.include_router(legal.router)
    app.include_router(two_factor.router)
    app.include_router(two_factor.admin_router)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


# How often the worker looks for jobs nobody is working on, and how long a job
# must sit untouched first. The worker refreshes a running job with every page;
# one page's provider call can take many minutes (with SDK retries), so a running
# job is presumed abandoned only well after that (ADR 0017).
JOB_SWEEP_SECONDS = 60.0
QUEUED_IDLE = timedelta(minutes=1)
RUNNING_IDLE = timedelta(minutes=45)


async def _recover_jobs(
    provider: ServicesProvider,
    *,
    queued_idle: timedelta = timedelta(0),
    running_idle: timedelta = timedelta(0),
) -> None:
    """Requeue jobs left queued or running with nobody working on them (ADR 0017).

    At startup every such job belongs to a process that is gone. In SQL mode this
    is its own unit of work: the recovery commits, then the jobs reach the worker.
    A failure is logged rather than raised, so a database that is briefly
    unavailable does not stop the API from starting or the sweep from running.
    """
    recovered = 0
    try:
        async for container in provider():
            recovered = await container.generation.recover_interrupted(
                queued_idle=queued_idle, running_idle=running_idle
            )
    except Exception:
        logger.exception("could not recover interrupted generation jobs")
        return
    if recovered:
        logger.info("requeued %d interrupted generation job(s)", recovered)


async def _sweep_jobs(provider: ServicesProvider) -> None:
    """Periodically pick up jobs another process left behind while this one runs.

    For example, during an overlapping deploy the old instance hands its running
    job back as QUEUED after this one has started.
    """
    while True:
        await asyncio.sleep(JOB_SWEEP_SECONDS)
        await _recover_jobs(provider, queued_idle=QUEUED_IDLE, running_idle=RUNNING_IDLE)


def _llm_from_settings(settings: Settings) -> LLMProvider:
    """Select the AI backend from settings, else a refusing stub (ADR 0006)."""
    from flashcards.adapters.llm import NullLLMProvider

    if settings.llm_provider == "fake":
        # Settings already refuses this outside tests; checked again here so a
        # Settings built without validation cannot slip it through (ADR 0021).
        refusal = fake_llm_refusal(settings.environment, settings.database_url)
        if refusal is not None:
            raise RuntimeError(refusal)
        from flashcards.adapters.llm import FAKE_LATENCY_SECONDS, FakeLLMProvider

        logger.warning("FAKE AI PROVIDER (tests only): card generation returns fixed drafts.")
        return FakeLLMProvider(latency_seconds=FAKE_LATENCY_SECONDS)
    if settings.llm_provider == "openai_compatible":
        if settings.openai_base_url:
            from flashcards.adapters.openai_llm import OpenAICompatibleLLMProvider

            return OpenAICompatibleLLMProvider(
                base_url=settings.openai_base_url,
                model=settings.openai_model,
                api_key=settings.openai_api_key,
                timeout_seconds=settings.openai_timeout_seconds,
            )
        logger.warning(
            "llm_provider is openai_compatible but openai_base_url is not set; "
            "AI requests will be refused."
        )
        return NullLLMProvider()
    if settings.anthropic_api_key:
        from flashcards.adapters.anthropic_llm import AnthropicLLMProvider

        return AnthropicLLMProvider(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
    logger.warning("No AI backend is configured; AI requests will be refused.")
    return NullLLMProvider()


def _transcripts_from_settings(settings: Settings) -> TranscriptProvider:
    """The YouTube fetcher when the operator turned it on, else the declining stub."""
    if not settings.youtube_transcripts_enabled:
        return NullTranscriptProvider()
    from flashcards.adapters.youtube_transcripts import YouTubeTranscriptProvider

    return YouTubeTranscriptProvider(
        languages=parse_language_preference(settings.youtube_transcript_languages),
        timeout_seconds=settings.youtube_transcript_timeout_seconds,
    )


def _to_micro_usd(usd: float | None) -> int | None:
    return None if usd is None else round(usd * MICRO_USD_PER_USD)


def _metering_from_settings(settings: Settings, *, ai_enabled: bool) -> AiMetering:
    """Usage records name the configured backend, priced at the operator's rates."""
    return AiMetering(
        provider=settings.llm_provider if ai_enabled else "none",
        prices=TokenPrices(
            input_usd_per_mtok=settings.ai_input_usd_per_mtok,
            output_usd_per_mtok=settings.ai_output_usd_per_mtok,
        ),
        default_quotas=InstanceQuotas(
            default_monthly_pages=settings.ai_default_monthly_pages,
            monthly_spend_cap_micro_usd=_to_micro_usd(settings.ai_monthly_spend_cap_usd),
        ),
    )


def _disclosure_from_settings(settings: Settings, *, ai_enabled: bool) -> AiDisclosure:
    return ai_disclosure(
        provider_key=settings.llm_provider if ai_enabled else None,
        name=settings.ai_provider_name,
        terms_url=settings.ai_provider_terms_url,
    )


def _mailer_from_settings(settings: Settings) -> Mailer:
    """SMTP when configured; otherwise a log-only mailer (full content in development)."""
    from flashcards.adapters.mailers import LogMailer, SmtpMailer

    if settings.smtp_host:
        return SmtpMailer(
            host=settings.smtp_host,
            port=settings.smtp_port,
            sender=settings.smtp_sender,
            username=settings.smtp_username,
            password=settings.smtp_password,
            security=settings.smtp_security,
        )
    if settings.environment != "development":
        logger.warning("No SMTP server configured; verification and reset emails are not sent.")
    return LogMailer(show_content=settings.environment == "development")


def _warn_on_local_links(settings: Settings) -> None:
    """Emails would carry links nobody outside this machine can open."""
    if settings.environment != "development" and "localhost" in settings.public_base_url:
        logger.warning(
            "FLASHCARDS_PUBLIC_BASE_URL is %s; links in emails will not work for users.",
            settings.public_base_url,
        )


@runtime_checkable
class Closeable(Protocol):
    """A provider holding a network client that should be closed on shutdown."""

    async def aclose(self) -> None: ...


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
