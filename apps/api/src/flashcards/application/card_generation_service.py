# SPDX-License-Identifier: AGPL-3.0-only
"""Run one AI generation job end to end (F-AI-1, F-AI-4, F-AI-10).

A worker calls run(job_id). The job row is the source of truth for progress, so
an SSE endpoint polls it and a boot-time requeue can resume from completed_units.
Each page is generated under a fresh schema-constrained call, the drafts are
stored for review (never scheduled yet), and the job advances. Cancellation is
cooperative: the flag is checked between pages.
"""

from __future__ import annotations

import uuid

from flashcards.application.ai_prompts import prompt_for
from flashcards.domain.ai import (
    DRAFT_CARDS_SCHEMA,
    DraftCardRecord,
    LlmDocument,
    StructuredRequest,
    parse_draft_cards,
)
from flashcards.domain.jobs import AiJob, JobStatus
from flashcards.domain.ports import (
    AiJobRepository,
    Clock,
    DocumentExtractor,
    DraftCardRepository,
    LLMProvider,
    ObjectStore,
)
from flashcards.domain.sources import ExtractedPage, PageKind

MAX_OUTPUT_TOKENS = 4096


class CardGenerationService:
    def __init__(
        self,
        jobs: AiJobRepository,
        drafts: DraftCardRepository,
        store: ObjectStore,
        extractor: DocumentExtractor,
        llm: LLMProvider,
        clock: Clock,
        max_pages: int = 20,
    ) -> None:
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
        try:
            await self._process(job)
        except Exception as exc:  # any failure is captured on the job, never raised
            # Fail the latest job state, not the stale claimed one, so the progress
            # _process persisted (completed_units, total_units) survives the failure
            # and a retry can resume. Only a still-RUNNING job can go to FAILED, which
            # it always is here (a job it finishes any other way returns, never raises).
            current = await self._jobs.get(job_id)
            if current is None or current.status is not JobStatus.RUNNING:
                return  # pragma: no cover - defensive: the claimed job is RUNNING here
            await self._jobs.update(current.failed(str(exc), self._clock.now()))

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
            await self._jobs.update(
                job.failed("the document has no readable pages", self._clock.now())
            )
            return

        # On a retry, completed_units marks the pages already generated on an earlier
        # attempt; resume from there so their drafts are not regenerated and duplicated.
        resume_from = job.completed_units
        await self._jobs.update(job.with_total(len(pages), self._clock.now()))

        for index, page in enumerate(pages):
            if index < resume_from:
                continue
            current = await self._jobs.get(job.id)
            if current is None:
                return  # pragma: no cover - the job cannot vanish mid-run
            if current.cancel_requested:
                await self._jobs.update(current.cancelled(self._clock.now()))
                return
            records = await self._generate_page(current, page)
            for record in records:
                await self._drafts.add(record)
            # Re-read before advancing: generation is the slow step, so a cancel can
            # arrive during it. Advancing a fresh read records progress AND preserves
            # that flag, where writing the pre-generation snapshot would wipe it.
            latest = await self._jobs.get(job.id)
            if latest is None:
                return  # pragma: no cover - the job cannot vanish mid-run
            if latest.cancel_requested:
                await self._jobs.update(latest.cancelled(self._clock.now()))
                return  # this page's drafts are kept; later pages are not generated
            await self._jobs.update(latest.advanced(self._clock.now()))

        final = await self._jobs.get(job.id)
        if final is None:
            return  # pragma: no cover - the job cannot vanish mid-run
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
