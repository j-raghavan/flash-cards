# SPDX-License-Identifier: AGPL-3.0-only
"""Starting and cancelling an AI generation job (F-AI-1, F-AI-10, F-AI-11)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from flashcards.application.ai_spend import AiSpend
from flashcards.application.deck_service import DeckService
from flashcards.application.quota_service import QuotaService
from flashcards.domain.ai import Thoroughness
from flashcards.domain.jobs import (
    AiJob,
    IllegalJobTransition,
    JobNotFound,
    JobStatus,
    new_generation_job,
)
from flashcards.domain.ports import (
    AiJobRepository,
    Clock,
    DocumentExtractor,
    JobQueue,
    ObjectStore,
    QuotaRepository,
)
from flashcards.domain.quotas import UNMETERED_PAGE_LIMIT, QuotaExceeded, billing_period
from flashcards.domain.sources import (
    DEFAULT_MAX_UPLOAD_PAGES,
    TooManyPages,
    clean_source_name,
    page_cap,
    source_name_key,
)
from flashcards.domain.transcripts import (
    TRANSCRIPT_MEDIA_TYPE,
    Transcript,
    chunk_transcript,
    encode_transcript,
)

DEFAULT_JOB_PAGE = 20
MAX_JOB_PAGE = 50


class GenerationRequestService:
    def __init__(
        self,
        decks: DeckService,
        extractor: DocumentExtractor,
        store: ObjectStore,
        quotas: QuotaRepository,
        jobs: AiJobRepository,
        queue: JobQueue,
        clock: Clock,
        limits: QuotaService,
        spend: AiSpend,
        max_pages: int = DEFAULT_MAX_UPLOAD_PAGES,
    ) -> None:
        self._decks = decks
        self._extractor = extractor
        self._store = store
        self._quotas = quotas
        self._jobs = jobs
        self._queue = queue
        self._clock = clock
        self._limits = limits
        self._spend = spend
        self._max_pages = max_pages

    async def start(
        self,
        *,
        owner_id: str,
        deck_id: str,
        data: bytes,
        thoroughness: Thoroughness,
        source_name: str | None = None,
        admin: bool = False,
    ) -> AiJob:
        """Validate and enqueue a generation job; raises before spending quota on bad input.

        source_name is the uploaded file's name, kept only to label chat citations.
        admin is the caller's role as read for this request: an admin's document
        has no page cap and is not held to the monthly page quota (ADR 0022).
        """
        # Raises DeckNotFound if the deck is missing or not the caller's; the drafts
        # are approved into this deck later (F-AI-4).
        await self._decks.get(owner_id=owner_id, deck_id=deck_id)
        # Raises UnsupportedMediaType on a disallowed type.
        media = self._extractor.sniff(data)
        pages = await self._extractor.count_pages(data)
        return await self._enqueue(
            owner_id,
            deck_id,
            data,
            media,
            pages,
            thoroughness,
            clean_source_name(source_name),
            admin=admin,
        )

    async def start_transcript(
        self,
        *,
        owner_id: str,
        deck_id: str,
        transcript: Transcript,
        thoroughness: Thoroughness,
        admin: bool = False,
    ) -> AiJob:
        """Enqueue generation from a video or pasted transcript (F-AI-2).

        Each time-windowed chunk counts as one page against the quota and the cap.
        """
        await self._decks.get(owner_id=owner_id, deck_id=deck_id)
        data = encode_transcript(transcript)  # raises InvalidTranscript
        pages = len(chunk_transcript(transcript))
        name = (
            "Pasted transcript"
            if transcript.video_id is None
            else clean_source_name(f"YouTube video {transcript.video_id}")
        )
        return await self._enqueue(
            owner_id, deck_id, data, TRANSCRIPT_MEDIA_TYPE, pages, thoroughness, name, admin=admin
        )

    async def ensure_pages_left(self, owner_id: str, *, admin: bool = False) -> None:
        """Raise SpendCapReached or QuotaExceeded when no generation could start now.

        A cheap check before slow work (a transcript fetch) that would otherwise be
        wasted; the reservation itself still happens atomically in _enqueue.
        """
        await self._spend.ensure_available()
        if admin:
            return  # the spend cap above applies to admins; the page quota does not
        period = billing_period(self._clock.now())
        limit = await self._limits.page_limit(owner_id)
        if limit is None:
            return  # the member has no page limit (ADR 0027); only the spend cap applies
        if await self._quotas.used(owner_id, period) >= limit:
            raise QuotaExceeded(remaining=0, limit=limit)

    async def _enqueue(
        self,
        owner_id: str,
        deck_id: str,
        data: bytes,
        media: str,
        pages: int,
        thoroughness: Thoroughness,
        name: str | None,
        *,
        admin: bool,
    ) -> AiJob:
        max_pages = page_cap(self._max_pages, admin=admin)
        if max_pages is not None and pages > max_pages:
            raise TooManyPages(pages, max_pages, transcript=media == TRANSCRIPT_MEDIA_TYPE)

        # The instance spend cap is the operator's guard on money: it applies to
        # admins too (ADR 0022). Raises SpendCapReached.
        await self._spend.ensure_available()
        now = self._clock.now()
        period = billing_period(now)
        # An admin's pages, and those of a member with no page limit, are still
        # counted so the usage report shows them, but against a limit that never
        # refuses them (ADR 0022, 0027).
        effective = None if admin else await self._limits.page_limit(owner_id)
        limit = UNMETERED_PAGE_LIMIT if effective is None else effective
        if not await self._quotas.try_reserve_pages(owner_id, pages, period, limit=limit):
            remaining = max(limit - await self._quotas.used(owner_id, period), 0)
            raise QuotaExceeded(pages=pages, remaining=remaining, limit=limit)

        source_id = str(uuid.uuid4())
        await self._store.put(source_id, data, media)
        if name is not None:
            await self._store.put(source_name_key(source_id), name.encode("utf-8"), "text/plain")
        job = new_generation_job(
            owner_id=owner_id,
            source_id=source_id,
            draft_deck_id=deck_id,
            thoroughness=thoroughness,
            now=now,
        )
        await self._jobs.add(job)
        await self._queue.enqueue(job.id)
        return job

    async def get(self, *, owner_id: str, job_id: str) -> AiJob:
        job = await self._jobs.get(job_id)
        if job is None or job.owner_id != owner_id:
            raise JobNotFound(job_id)
        return job

    async def list_recent(
        self,
        *,
        owner_id: str,
        deck_id: str | None = None,
        limit: int = DEFAULT_JOB_PAGE,
        offset: int = 0,
    ) -> list[AiJob]:
        """A page of the owner's jobs, newest first, at most MAX_JOB_PAGE long."""
        return await self._jobs.list_recent(
            owner_id,
            deck_id=deck_id,
            limit=min(max(limit, 1), MAX_JOB_PAGE),
            offset=max(offset, 0),
        )

    async def retry(self, *, owner_id: str, job_id: str) -> AiJob:
        """Queue a failed job again (ADR 0012); raises IllegalJobTransition otherwise.

        The job resumes from the pages it already generated, so they are neither
        generated nor charged again.
        """
        job = await self.get(owner_id=owner_id, job_id=job_id)
        await self._spend.ensure_available()  # a retry is new AI work
        queued = await self._jobs.try_retry(job.id, self._clock.now())
        if queued is None:
            raise IllegalJobTransition(f"cannot retry from {job.status}")
        await self._queue.enqueue(job.id)
        return queued

    async def recover_interrupted(
        self,
        *,
        queued_idle: timedelta = timedelta(0),
        running_idle: timedelta = timedelta(0),
    ) -> int:
        """Hand jobs nobody is working on to this worker; return how many (ADR 0017).

        Only jobs untouched for at least the given idle time are considered: at
        startup both are zero (anything queued or running belongs to a process that
        is gone); a periodic sweep uses longer times. The worker refreshes a job's
        updated_at with every page, so a RUNNING job idle that long has lost its
        runner.

        QUEUED jobs are enqueued again (the queue lives in memory, and claiming is
        conditional, so a duplicate is harmless). RUNNING jobs are recovered as the
        next attempt and resume from completed_units, so pages already generated are
        neither regenerated nor charged again; a job whose attempts are spent fails
        instead, so a job that crashes the process cannot loop forever. Recovery is
        conditional, so a job that finished meanwhile is left alone, and a runner
        elsewhere that still holds the old attempt stops at its next progress check.
        """
        now = self._clock.now()
        recovered = [
            job.id
            for job in await self._jobs.list_queued()
            if job.updated_at <= now - queued_idle
        ]
        for job in await self._jobs.list_running():
            if job.updated_at > now - running_idle:
                continue
            after = await self._jobs.try_requeue_interrupted(job.id, job.attempt, now)
            if after is not None and after.status is JobStatus.QUEUED:
                recovered.append(job.id)
        for job_id in recovered:
            await self._queue.enqueue(job_id)
        return len(recovered)

    async def cancel(self, *, owner_id: str, job_id: str) -> None:
        await self.get(owner_id=owner_id, job_id=job_id)  # ownership check
        await self._jobs.request_cancel(job_id, self._clock.now())
