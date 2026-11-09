# SPDX-License-Identifier: AGPL-3.0-only
"""Starting and cancelling an AI generation job (F-AI-1, F-AI-10, F-AI-11)."""

from __future__ import annotations

import uuid

from flashcards.application.deck_service import DeckService
from flashcards.domain.ai import Thoroughness
from flashcards.domain.jobs import AiJob, IllegalJobTransition, JobNotFound, new_generation_job
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
        max_pages: int = DEFAULT_MAX_UPLOAD_PAGES,
    ) -> None:
        self._decks = decks
        self._extractor = extractor
        self._store = store
        self._quotas = quotas
        self._jobs = jobs
        self._queue = queue
        self._clock = clock
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

        now = self._clock.now()
        if not await self._quotas.try_reserve_pages(owner_id, pages, billing_period(now)):
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
        queued = await self._jobs.try_retry(job.id, self._clock.now())
        if queued is None:
            raise IllegalJobTransition(f"cannot retry from {job.status}")
        await self._queue.enqueue(job.id)
        return queued

    async def recover_interrupted(self) -> int:
        """Hand jobs left behind by a stopped process to the worker; return how many.

        Run once at startup, before the worker starts. QUEUED jobs are simply
        enqueued again (the queue lives in memory). RUNNING jobs are requeued as a
        new attempt and resume from completed_units, so pages already generated are
        neither regenerated nor charged again, and the requeue is conditional, so a
        job that finished meanwhile is left alone. A runner in another process that
        still holds the old attempt stops at its next progress check.
        """
        now = self._clock.now()
        recovered = [job.id for job in await self._jobs.list_queued()]
        for job in await self._jobs.list_running():
            if await self._jobs.try_requeue_interrupted(job.id, job.attempt, now) is not None:
                recovered.append(job.id)
        for job_id in recovered:
            await self._queue.enqueue(job_id)
        return len(recovered)

    async def cancel(self, *, owner_id: str, job_id: str) -> None:
        await self.get(owner_id=owner_id, job_id=job_id)  # ownership check
        await self._jobs.request_cancel(job_id, self._clock.now())
