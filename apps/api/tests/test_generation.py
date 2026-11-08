# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from flashcards.adapters.memory import (
    InMemoryAiJobRepository,
    InMemoryDraftCardRepository,
    InMemoryObjectStore,
)
from flashcards.application.card_generation_service import CardGenerationService
from flashcards.domain.ai import (
    LlmError,
    StructuredRequest,
    StructuredResult,
    Thoroughness,
    TokenUsage,
)
from flashcards.domain.jobs import JobStatus, new_generation_job
from flashcards.domain.sources import ExtractedPage, PageKind, SourceRef, TooManyPages

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
ALICE = "alice"


class FakeClock:
    def now(self) -> datetime:
        return NOW


def _text_page(page: int) -> ExtractedPage:
    return ExtractedPage(
        ref=SourceRef(file_id="src", page=page), kind=PageKind.TEXT, text=f"p{page}"
    )


def _image_page(page: int) -> ExtractedPage:
    return ExtractedPage(
        ref=SourceRef(file_id="src", page=page),
        kind=PageKind.IMAGE,
        image=b"\x89PNG-fake",
        image_media_type="image/png",
    )


class FakeExtractor:
    def __init__(self, pages: list[ExtractedPage], error: Exception | None = None) -> None:
        self._pages = pages
        self._error = error

    def sniff(self, data: bytes) -> str:
        return "application/pdf"

    def count_pages(self, data: bytes) -> int:
        return len(self._pages)

    async def extract(
        self, data: bytes, *, file_id: str, max_pages: int
    ) -> AsyncIterator[ExtractedPage]:
        if self._error is not None:
            raise self._error
        for page in self._pages:
            yield page


class FakeLLM:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.seen: list[StructuredRequest] = []

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        self.seen.append(request)
        if self._error is not None:
            raise self._error
        return StructuredResult(
            data={"cards": [{"type": "basic", "fields": {"front": "Q", "back": "A"}}]},
            usage=TokenUsage(input_tokens=10, output_tokens=20),
            model="fake",
        )


def _service(
    jobs: InMemoryAiJobRepository,
    drafts: InMemoryDraftCardRepository,
    store: InMemoryObjectStore,
    extractor: FakeExtractor,
    llm: FakeLLM,
) -> CardGenerationService:
    return CardGenerationService(jobs, drafts, store, extractor, llm, FakeClock())


async def _seed_job(jobs: InMemoryAiJobRepository, store: InMemoryObjectStore) -> str:
    job = new_generation_job(
        owner_id=ALICE,
        source_id="src",
        draft_deck_id="deck",
        thoroughness=Thoroughness.STANDARD,
        now=NOW,
    )
    await jobs.add(job)
    await store.put("src", b"%PDF-fake", "application/pdf")
    return job.id


async def test_generation_succeeds_and_stores_drafts() -> None:
    jobs, drafts, store = (
        InMemoryAiJobRepository(),
        InMemoryDraftCardRepository(),
        InMemoryObjectStore(),
    )
    llm = FakeLLM()
    # One born-digital text page and one image page, to exercise both document kinds.
    service = _service(jobs, drafts, store, FakeExtractor([_text_page(1), _image_page(2)]), llm)
    job_id = await _seed_job(jobs, store)

    await service.run(job_id)

    job = await jobs.get(job_id)
    assert job is not None
    assert job.status is JobStatus.SUCCEEDED
    assert job.completed_units == 2
    assert job.progress == 1.0
    records = await drafts.list_for_job(job_id)
    assert [r.source_page for r in records] == [1, 2]
    # Uploaded content went in as a document, not in the instructions.
    assert "p1" not in llm.seen[0].instructions
    assert llm.seen[0].documents[0].data == b"p1"
    assert llm.seen[0].documents[0].media_type == "text/plain"
    assert llm.seen[1].documents[0].data == b"\x89PNG-fake"
    assert llm.seen[1].documents[0].media_type == "image/png"


async def test_cancel_requested_stops_before_generating() -> None:
    jobs, drafts, store = (
        InMemoryAiJobRepository(),
        InMemoryDraftCardRepository(),
        InMemoryObjectStore(),
    )
    extractor = FakeExtractor([_text_page(1), _text_page(2)])
    service = _service(jobs, drafts, store, extractor, FakeLLM())
    job_id = await _seed_job(jobs, store)
    await jobs.request_cancel(job_id, NOW)

    await service.run(job_id)

    job = await jobs.get(job_id)
    assert job is not None and job.status is JobStatus.CANCELLED
    assert await drafts.list_for_job(job_id) == []


async def test_llm_failure_marks_job_failed() -> None:
    jobs, drafts, store = (
        InMemoryAiJobRepository(),
        InMemoryDraftCardRepository(),
        InMemoryObjectStore(),
    )
    service = _service(
        jobs, drafts, store, FakeExtractor([_text_page(1)]), FakeLLM(error=LlmError("boom"))
    )
    job_id = await _seed_job(jobs, store)

    await service.run(job_id)

    job = await jobs.get(job_id)
    assert job is not None and job.status is JobStatus.FAILED
    assert job.error == "boom"


async def test_missing_source_marks_job_failed() -> None:
    jobs, drafts = InMemoryAiJobRepository(), InMemoryDraftCardRepository()
    store = InMemoryObjectStore()  # nothing stored
    service = _service(jobs, drafts, store, FakeExtractor([_text_page(1)]), FakeLLM())
    job = new_generation_job(
        owner_id=ALICE,
        source_id="missing",
        draft_deck_id="deck",
        thoroughness=Thoroughness.CONCISE,
        now=NOW,
    )
    await jobs.add(job)

    await service.run(job.id)

    stored = await jobs.get(job.id)
    assert stored is not None and stored.status is JobStatus.FAILED
    assert "not found" in (stored.error or "")


async def test_extractor_failure_marks_job_failed() -> None:
    jobs, drafts, store = (
        InMemoryAiJobRepository(),
        InMemoryDraftCardRepository(),
        InMemoryObjectStore(),
    )
    service = _service(
        jobs, drafts, store, FakeExtractor([], error=TooManyPages("too many")), FakeLLM()
    )
    job_id = await _seed_job(jobs, store)

    await service.run(job_id)

    job = await jobs.get(job_id)
    assert job is not None and job.status is JobStatus.FAILED


async def test_run_is_a_noop_when_not_claimable() -> None:
    jobs, drafts, store = (
        InMemoryAiJobRepository(),
        InMemoryDraftCardRepository(),
        InMemoryObjectStore(),
    )
    service = _service(jobs, drafts, store, FakeExtractor([_text_page(1)]), FakeLLM())
    job_id = await _seed_job(jobs, store)

    await service.run(job_id)
    first = await jobs.get(job_id)
    assert first is not None and first.status is JobStatus.SUCCEEDED
    # A second run cannot re-claim a finished job; nothing changes.
    await service.run(job_id)
    again = await jobs.get(job_id)
    assert again is not None and again.status is JobStatus.SUCCEEDED
    assert len(await drafts.list_for_job(job_id)) == 1
