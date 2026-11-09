# SPDX-License-Identifier: AGPL-3.0-only
"""Run one AI generation job end to end (F-AI-1, F-AI-4, F-AI-10).

A worker calls run(job_id). The job row is the source of truth for progress, so
an SSE endpoint polls it and a boot-time requeue can resume from completed_units.
Each page is generated under a fresh schema-constrained call, the drafts are
stored for review (never scheduled yet), and the job advances. Cancellation is
cooperative: the flag is checked between pages.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from flashcards.application.ai_prompts import prompt_for
from flashcards.domain.ai import (
    DRAFT_CARDS_SCHEMA,
    DraftCardRecord,
    InvalidModelOutput,
    LlmDocument,
    LlmError,
    StructuredRequest,
    parse_draft_cards,
)
from flashcards.domain.jobs import AiJob, JobFailure, JobStatus
from flashcards.domain.ports import (
    AiJobRepository,
    Clock,
    DocumentExtractor,
    DraftCardRepository,
    LLMProvider,
    ObjectStore,
    UnitOfWork,
)
from flashcards.domain.quotas import AiCallLimitReached, QuotaExceeded
from flashcards.domain.sources import (
    DEFAULT_MAX_UPLOAD_PAGES,
    ExtractedPage,
    PageKind,
    TooManyPages,
    UnreadableDocument,
    UnsupportedMediaType,
)
from flashcards.domain.transcripts import InvalidTranscript

MAX_OUTPUT_TOKENS = 4096

logger = logging.getLogger("flashcards")

_DOCUMENT_ERRORS = (UnreadableDocument, UnsupportedMediaType, TooManyPages, InvalidTranscript)
_QUOTA_ERRORS = (QuotaExceeded, AiCallLimitReached)


class _Superseded(Exception):
    """This run's job was requeued as a new attempt, or vanished, while it ran."""


def _owned(latest: AiJob | None, run: AiJob) -> AiJob | None:
    """The stored job if it still belongs to this run (same RUNNING attempt), else None."""
    if latest is None or latest.status is not JobStatus.RUNNING or latest.attempt != run.attempt:
        return None
    return latest


def classify_failure(exc: Exception) -> JobFailure:
    """The user-facing category for a failure; the exception text itself is never shown."""
    if isinstance(exc, InvalidModelOutput):
        return JobFailure.BAD_MODEL_OUTPUT
    if isinstance(exc, LlmError):
        return JobFailure.PROVIDER_UNAVAILABLE
    if isinstance(exc, _DOCUMENT_ERRORS):
        return JobFailure.UNREADABLE_DOCUMENT
    if isinstance(exc, _QUOTA_ERRORS):
        return JobFailure.QUOTA_EXCEEDED
    return JobFailure.INTERNAL


def _log_failure(job_id: str, exc: Exception) -> None:
    # The details stay in the server log: expected failures as warnings, anything
    # else with its traceback as an error.
    failure = classify_failure(exc)
    if failure is JobFailure.INTERNAL:
        logger.exception("generation job %s failed unexpectedly", job_id)
    else:
        logger.warning("generation job %s failed (%s): %s", job_id, failure.name, exc)


class CardGenerationService:
    def __init__(
        self,
        jobs: AiJobRepository,
        drafts: DraftCardRepository,
        store: ObjectStore,
        extractor: DocumentExtractor,
        llm: LLMProvider,
        clock: Clock,
        unit_of_work: UnitOfWork,
        max_pages: int = DEFAULT_MAX_UPLOAD_PAGES,
    ) -> None:
        self._uow = unit_of_work
        self._jobs = jobs
        self._drafts = drafts
        self._store = store
        self._extractor = extractor
        self._llm = llm
        self._clock = clock
        self._max_pages = max_pages

    async def run(self, job_id: str) -> None:
        job = await self._jobs.try_claim(job_id, self._clock.now())
        if job is None:
            return  # already running, finished, or gone: never process twice
        # Progress is committed as it happens (ADR 0012), so progress events and
        # GET /ai/jobs/{id} see it, and the job's row is not locked while the slow
        # LLM calls run: a cancel or retry never has to wait for the whole job.
        await self._uow.commit()
        try:
            await self._process(job)
        except asyncio.CancelledError:
            # The worker is stopping (shutdown or deploy): hand the job back so the
            # next worker resumes it instead of leaving it RUNNING with nobody on it.
            await self._release(job)
            raise
        except _Superseded:
            # The job was requeued after this run was presumed interrupted (ADR 0017):
            # the new run owns it now. Drop this page's work and write nothing.
            await self._uow.rollback()
            logger.info("generation job %s attempt %d was superseded", job_id, job.attempt)
        except Exception as exc:  # any failure is captured on the job, never raised
            _log_failure(job_id, exc)
            # Discard the failed page's partial work (its drafts were not counted),
            # then fail the latest committed state, so completed_units and
            # total_units survive and a retry resumes without duplicating drafts.
            await self._uow.rollback()
            current = _owned(await self._jobs.get(job_id), job)
            if current is None:
                return  # finished, cancelled or superseded meanwhile: not ours to fail
            await self._jobs.update(current.failed(classify_failure(exc), self._clock.now()))

    async def _release(self, job: AiJob) -> None:
        try:
            await self._uow.rollback()  # the in-flight page is regenerated next time
            await self._jobs.try_requeue_interrupted(job.id, job.attempt, self._clock.now())
            await self._uow.commit()
        except Exception:
            # Startup recovery picks the job up later; never mask the cancellation.
            logger.exception("could not release generation job %s on shutdown", job.id)

    async def _current(self, job: AiJob) -> AiJob:
        """Re-read the job, or raise _Superseded if this run no longer owns it."""
        latest = _owned(await self._jobs.get(job.id), job)
        if latest is None:
            raise _Superseded()
        return latest

    async def _process(self, job: AiJob) -> None:
        data = await self._store.get(job.source_id)
        if data is None:
            raise FileNotFoundError("source document not found")

        pages = [
            page
            async for page in self._extractor.extract(
                data, file_id=job.source_id, max_pages=self._max_pages
            )
        ]
        if not pages:
            # An empty/garbage upload must not look like a done job stuck at 0%.
            current = await self._current(job)
            await self._jobs.update(
                current.failed(JobFailure.NO_READABLE_PAGES, self._clock.now())
            )
            return

        # On a retry, completed_units marks the pages already generated on an earlier
        # attempt; resume from there so their drafts are not regenerated and duplicated.
        resume_from = job.completed_units
        current = await self._current(job)
        await self._jobs.update(current.with_total(len(pages), self._clock.now()))
        await self._uow.commit()

        for index, page in enumerate(pages):
            if index < resume_from:
                continue
            current = await self._current(job)
            if current.cancel_requested:
                await self._jobs.update(current.cancelled(self._clock.now()))
                return
            records = await self._generate_page(current, page)
            for record in records:
                await self._drafts.add(record)
            # Re-read before advancing: generation is the slow step, so a cancel or a
            # requeue can arrive during it. Advancing a fresh read records progress
            # AND preserves a cancel flag, where writing the pre-generation snapshot
            # would wipe it; a requeued job raises _Superseded and its page is dropped.
            latest = await self._current(job)
            if latest.cancel_requested:
                await self._jobs.update(latest.cancelled(self._clock.now()))
                return  # this page's drafts are kept; later pages are not generated
            await self._jobs.update(latest.advanced(self._clock.now()))
            await self._uow.commit()  # this page's drafts and its progress, together

        final = await self._current(job)
        await self._jobs.update(final.succeeded(self._clock.now()))

    async def _generate_page(self, job: AiJob, page: ExtractedPage) -> list[DraftCardRecord]:
        request = StructuredRequest(
            instructions=prompt_for(job.thoroughness),
            documents=(_page_document(page),),
            schema=DRAFT_CARDS_SCHEMA,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        result = await self._llm.generate_structured(request)
        drafts = parse_draft_cards(result.data, source_page=page.ref.page)
        now = self._clock.now()
        return [
            DraftCardRecord(
                id=str(uuid.uuid4()),
                job_id=job.id,
                owner_id=job.owner_id,
                note_type=draft.note_type,
                fields=draft.fields,
                source_page=draft.source_page,
                created_at=now,
            )
            for draft in drafts
        ]


def _page_document(page: ExtractedPage) -> LlmDocument:
    if page.kind is PageKind.TEXT:
        return LlmDocument(media_type="text/plain", data=(page.text or "").encode("utf-8"))
    return LlmDocument(
        media_type=page.image_media_type or "image/png", data=page.image or b""
    )
