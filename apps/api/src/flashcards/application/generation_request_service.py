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
from flashcards.domain.quotas import QuotaExceeded, billing_period
from flashcards.domain.sources import DEFAULT_MAX_UPLOAD_PAGES, TooManyPages
from flashcards.domain.transcripts import (
    TRANSCRIPT_MEDIA_TYPE,
    Transcript,
    chunk_transcript,
    encode_transcript,
)


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
        self, *, owner_id: str, deck_id: str, data: bytes, thoroughness: Thoroughness
    ) -> AiJob:
        """Validate and enqueue a generation job; raises before spending quota on bad input."""
        # Raises DeckNotFound if the deck is missing or not the caller's; the drafts
        # are approved into this deck later (F-AI-4).
        await self._decks.get(owner_id=owner_id, deck_id=deck_id)
        # Raises UnsupportedMediaType on a disallowed type.
        media = self._extractor.sniff(data)
        pages = await self._extractor.count_pages(data)
        return await self._enqueue(owner_id, deck_id, data, media, pages, thoroughness)

    async def start_transcript(
        self,
        *,
        owner_id: str,
        deck_id: str,
        transcript: Transcript,
        thoroughness: Thoroughness,
    ) -> AiJob:
        """Enqueue generation from a video or pasted transcript (F-AI-2).

        Each time-windowed chunk counts as one page against the quota and the cap.
        """
        await self._decks.get(owner_id=owner_id, deck_id=deck_id)
        data = encode_transcript(transcript)  # raises InvalidTranscript
        pages = len(chunk_transcript(transcript))
        return await self._enqueue(
            owner_id, deck_id, data, TRANSCRIPT_MEDIA_TYPE, pages, thoroughness
        )

    async def _enqueue(
        self,
        owner_id: str,
        deck_id: str,
        data: bytes,
        media: str,
        pages: int,
        thoroughness: Thoroughness,
    ) -> AiJob:
        if pages > self._max_pages:
            raise TooManyPages(f"{pages} pages (max {self._max_pages})")

        await self._spend.ensure_available()  # raises SpendCapReached
        now = self._clock.now()
        limit = await self._limits.page_limit(owner_id)
        if not await self._quotas.try_reserve_pages(
            owner_id, pages, billing_period(now), limit=limit
        ):
            raise QuotaExceeded(f"{pages} pages exceeds the remaining quota")

        source_id = str(uuid.uuid4())
        await self._store.put(source_id, data, media)
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
