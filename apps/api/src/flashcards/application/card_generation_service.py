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
from contextlib import aclosing

from flashcards.application.ai_prompts import prompt_for
from flashcards.application.ai_spend import AiSpend
from flashcards.domain.ai import (
    DRAFT_CARDS_SCHEMA,
    DraftCardRecord,
    DraftOutput,
    InvalidModelOutput,
    LlmDocument,
    LlmError,
    OutputTruncated,
    StructuredRequest,
    StructuredResult,
    parse_draft_output,
)
from flashcards.domain.figures import snap, with_figure
from flashcards.domain.jobs import AiJob, JobFailure, JobStatus
from flashcards.domain.media import MAX_MEDIA_BYTES, MediaAsset
from flashcards.domain.ports import (
    AiJobRepository,
    Clock,
    DocumentExtractor,
    DraftCardRepository,
    FigureCropper,
    LLMProvider,
    MediaRepository,
    ObjectStore,
    UnitOfWork,
    UserRepository,
)
from flashcards.domain.quotas import AiCallLimitReached, QuotaExceeded
from flashcards.domain.sources import (
    DEFAULT_MAX_UPLOAD_PAGES,
    ExtractedPage,
    PageKind,
    TooManyPages,
    UnreadableDocument,
    UnsupportedMediaType,
    page_cap,
)
from flashcards.domain.transcripts import InvalidTranscript
from flashcards.domain.usage import AiFeature, SpendCapReached

# Room for a full page of self-contained cards plus a reasoning model's hidden
# reasoning, which counts against the same limit on OpenAI's GPT-5 and o-series.
MAX_OUTPUT_TOKENS = 16_000

logger = logging.getLogger("flashcards")

_DOCUMENT_ERRORS = (UnreadableDocument, UnsupportedMediaType, InvalidTranscript)
_QUOTA_ERRORS = (QuotaExceeded, AiCallLimitReached, SpendCapReached)


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
    if isinstance(exc, TooManyPages):
        # The owner's cap at run time, e.g. an admin demoted after queueing it.
        return JobFailure.TOO_MANY_PAGES
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
        spend: AiSpend,
        *,
        users: UserRepository,
        max_pages: int = DEFAULT_MAX_UPLOAD_PAGES,
        media: MediaRepository | None = None,
        cropper: FigureCropper | None = None,
    ) -> None:
        # Figures a card shows are cropped from its page and stored as the owner's
        # media; without either collaborator cards keep their text only.
        self._media = media
        self._cropper = cropper
        self._uow = unit_of_work
        self._users = users
        self._spend = spend
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

        # The owner's role is read now, as a request would read it: an admin's
        # document has no page cap (ADR 0022).
        max_pages = await self._page_cap(job.owner_id)
        total = await self._extractor.count_pages(data)
        if max_pages is not None and total > max_pages:
            raise TooManyPages(total, max_pages)
        if total == 0:
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
        await self._jobs.update(current.with_total(total, self._clock.now()))
        await self._uow.commit()

        # Pages are extracted one at a time, each generated before the next is
        # rendered, so memory stays at about one page however long the document is
        # (ADR 0022). Pages already generated are skipped without being rendered.
        async with aclosing(
            self._extractor.extract(
                data, file_id=job.source_id, max_pages=max_pages, skip=resume_from
            )
        ) as pages:
            async for page in pages:
                current = await self._current(job)
                if current.cancel_requested:
                    await self._jobs.update(current.cancelled(self._clock.now()))
                    return
                records = await self._generate_page(current, page)
                for record in records:
                    await self._drafts.add(record)
                # Re-read before advancing: generation is the slow step, so a cancel or
                # a requeue can arrive during it. Advancing a fresh read records
                # progress AND preserves a cancel flag, where writing the
                # pre-generation snapshot would wipe it; a requeued job raises
                # _Superseded and its page is dropped.
                latest = await self._current(job)
                if latest.cancel_requested:
                    await self._jobs.update(latest.cancelled(self._clock.now()))
                    return  # this page's drafts are kept; later pages are not generated
                await self._jobs.update(latest.advanced(self._clock.now()))
                await self._uow.commit()  # this page's drafts and its progress, together

        final = await self._current(job)
        await self._jobs.update(final.succeeded(self._clock.now()))

    async def _page_cap(self, owner_id: str) -> int | None:
        owner = await self._users.get(owner_id)
        return page_cap(self._max_pages, admin=owner is not None and owner.is_active_admin)

    async def _generate_page(self, job: AiJob, page: ExtractedPage) -> list[DraftCardRecord]:
        # The model is told the figures measured on the page image it is shown.
        measured = page.figure_boxes if page.image is not None else ()
        request = StructuredRequest(
            instructions=prompt_for(job.thoroughness, measured),
            documents=_page_documents(page),
            schema=DRAFT_CARDS_SCHEMA,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        # Each page holds its estimated cost against the spend cap (ADR 0019); once
        # the cap is reached the job fails here and can be retried next month.
        hold = await self._spend.hold(request)
        try:
            result = await self._llm.generate_structured(request)
        except OutputTruncated as cut:
            # Billed although unusable: record what the provider reported, then fail.
            billed = StructuredResult(data={}, usage=cut.usage, model=cut.model)
            await self._spend.record(job.owner_id, AiFeature.GENERATION, billed, hold)
            await self._uow.commit()
            raise
        except BaseException:
            await self._spend.release(hold)
            raise
        # Recorded and committed before the output is parsed: the provider has
        # charged for the call even if its output turns out to be unusable.
        await self._spend.record(job.owner_id, AiFeature.GENERATION, result, hold)
        await self._uow.commit()
        output = parse_draft_output(result.data, source_page=page.ref.page)
        shown = await self._store_figures(job, page, output)
        now = self._clock.now()
        return [
            DraftCardRecord(
                id=str(uuid.uuid4()),
                job_id=job.id,
                owner_id=job.owner_id,
                note_type=draft.note_type,
                fields=(
                    with_figure(draft.note_type, draft.fields, shown[draft.figure])
                    if draft.figure in shown
                    else draft.fields
                ),
                source_page=draft.source_page,
                created_at=now,
            )
            for draft in output.cards
        ]

    async def _store_figures(
        self, job: AiJob, page: ExtractedPage, output: DraftOutput
    ) -> dict[int, str]:
        """Crop and store each figure a card shows; its 1-based number to its media id.

        A figure is cropped once however many cards show it, and goes into the same
        transaction as the page's drafts, so a failed page leaves no stray media. A
        figure that cannot be cropped is left out and its cards keep their text, as
        do cards on a page sent without an image.
        """
        if self._media is None or self._cropper is None or page.image is None:
            return {}
        wanted = {
            card.figure: box
            for card in output.cards
            if card.figure > 0 and (box := output.figures[card.figure - 1]) is not None
        }
        shown: dict[int, str] = {}
        for number, box in sorted(wanted.items()):
            clip = snap(box, page.figure_boxes)
            if clip is None:  # most of the page, not a figure: the cards keep their text
                continue
            try:
                data = await self._cropper.crop(page.image, clip)
            except Exception:  # an unreadable or oversized image: the cards keep their text
                logger.warning("could not crop figure %d of a page of job %s", number, job.id)
                continue
            if len(data) > MAX_MEDIA_BYTES:
                continue
            asset = MediaAsset(
                id=str(uuid.uuid4()),
                owner_id=job.owner_id,
                content_type="image/png",
                data=data,
                created_at=self._clock.now(),
            )
            await self._media.add(asset)
            shown[number] = asset.id
        return shown


def _page_documents(page: ExtractedPage) -> tuple[LlmDocument, ...]:
    """What the model is shown: a page's text, its image, or both when it has figures."""
    documents: list[LlmDocument] = []
    if page.kind is PageKind.TEXT:
        documents.append(
            LlmDocument(media_type="text/plain", data=(page.text or "").encode("utf-8"))
        )
    if page.image is not None or page.kind is PageKind.IMAGE:
        documents.append(LlmDocument(media_type=_image_type(page), data=page.image or b""))
    return tuple(documents)


def _image_type(page: ExtractedPage) -> str:
    return page.image_media_type or "image/png"
