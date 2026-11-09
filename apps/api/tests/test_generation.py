# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime

import pytest

from flashcards.adapters.memory import (
    InMemoryAiJobRepository,
    InMemoryDraftCardRepository,
    InMemoryObjectStore,
)
from flashcards.application.card_generation_service import (
    CardGenerationService,
    classify_failure,
)
from flashcards.domain.ai import (
    InvalidModelOutput,
    LlmError,
    StructuredRequest,
    StructuredResult,
    Thoroughness,
    TokenUsage,
)
from flashcards.domain.jobs import JobFailure, JobStatus, new_generation_job
from flashcards.domain.quotas import AiCallLimitReached, QuotaExceeded
from flashcards.domain.sources import (
    ExtractedPage,
    PageKind,
    SourceRef,
    TooManyPages,
    UnreadableDocument,
    UnsupportedMediaType,
)
from flashcards.domain.transcripts import InvalidTranscript

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

    async def count_pages(self, data: bytes) -> int:
        return len(self._pages)

    async def extract(
        self, data: bytes, *, file_id: str, max_pages: int
    ) -> AsyncIterator[ExtractedPage]:
        if self._error is not None:
            raise self._error
        for page in self._pages:
            yield page


class FakeLLM:
    def __init__(
        self,
        error: Exception | None = None,
        *,
        fail_on_call: int | None = None,
        after_call: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._error = error
        self._fail_on_call = fail_on_call
        self._after_call = after_call
        self.seen: list[StructuredRequest] = []

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        self.seen.append(request)
        if self._error is not None:
            raise self._error
        if self._fail_on_call is not None and len(self.seen) == self._fail_on_call:
            raise LlmError(f"failed on call {self._fail_on_call}")
        if self._after_call is not None:
            await self._after_call()
        return StructuredResult(
            data={"cards": [{"type": "basic", "fields": {"front": "Q", "back": "A"}}]},
            usage=TokenUsage(input_tokens=10, output_tokens=20),
            model="fake",
        )


class RecordingUnitOfWork:
    """Records commits and rollbacks with the job's progress at that moment."""

    def __init__(self, jobs: InMemoryAiJobRepository | None = None) -> None:
        self.jobs = jobs
        self.log: list[str] = []

    async def _state(self) -> str:
        if self.jobs is None:
            return ""
        job = (await self.jobs.list_for_owner(ALICE))[0]
        return f"{job.status.value}:{job.completed_units}/{job.total_units}"

    async def commit(self) -> None:
        self.log.append(f"commit {await self._state()}")

    async def rollback(self) -> None:
        self.log.append(f"rollback {await self._state()}")


def _service(
    jobs: InMemoryAiJobRepository,
    drafts: InMemoryDraftCardRepository,
    store: InMemoryObjectStore,
    extractor: FakeExtractor,
    llm: FakeLLM,
    uow: RecordingUnitOfWork | None = None,
) -> CardGenerationService:
    return CardGenerationService(
        jobs, drafts, store, extractor, llm, FakeClock(), uow or RecordingUnitOfWork()
    )


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
    assert job.error == JobFailure.PROVIDER_UNAVAILABLE.value


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
    assert stored.error == JobFailure.INTERNAL.value  # the detail is logged, not shown


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
    assert job.error == JobFailure.UNREADABLE_DOCUMENT.value


async def test_failure_midway_preserves_progress_and_resumes_on_retry() -> None:
    jobs, drafts, store = (
        InMemoryAiJobRepository(),
        InMemoryDraftCardRepository(),
        InMemoryObjectStore(),
    )
    extractor = FakeExtractor([_text_page(1), _text_page(2), _text_page(3)])
    # The LLM fails on the second page's call the first time through.
    failing_llm = FakeLLM(fail_on_call=2)
    job_id = await _seed_job(jobs, store)

    await _service(jobs, drafts, store, extractor, failing_llm).run(job_id)

    failed = await jobs.get(job_id)
    assert failed is not None and failed.status is JobStatus.FAILED
    # Page 1 succeeded, so the resume point and the page total survive the failure.
    assert (failed.completed_units, failed.total_units) == (1, 3)
    assert [r.source_page for r in await drafts.list_for_job(job_id)] == [1]

    # Retry with a healthy LLM: it resumes from page 2 and does not re-generate page 1.
    await jobs.update(failed.retried(NOW))
    await _service(jobs, drafts, store, extractor, FakeLLM()).run(job_id)

    done = await jobs.get(job_id)
    assert done is not None and done.status is JobStatus.SUCCEEDED
    assert [r.source_page for r in await drafts.list_for_job(job_id)] == [1, 2, 3]


async def test_zero_page_document_fails_rather_than_succeeding_at_zero() -> None:
    jobs, drafts, store = (
        InMemoryAiJobRepository(),
        InMemoryDraftCardRepository(),
        InMemoryObjectStore(),
    )
    service = _service(jobs, drafts, store, FakeExtractor([]), FakeLLM())
    job_id = await _seed_job(jobs, store)

    await service.run(job_id)

    job = await jobs.get(job_id)
    assert job is not None and job.status is JobStatus.FAILED
    assert job.error == JobFailure.NO_READABLE_PAGES.value
    assert await drafts.list_for_job(job_id) == []


async def test_cancel_between_pages_keeps_earlier_drafts() -> None:
    jobs, drafts, store = (
        InMemoryAiJobRepository(),
        InMemoryDraftCardRepository(),
        InMemoryObjectStore(),
    )
    job_id = await _seed_job(jobs, store)

    async def cancel_after_first_page() -> None:
        await jobs.request_cancel(job_id, NOW)

    # Cancel arrives while page 1 is generating; page 2 is never produced.
    llm = FakeLLM(after_call=cancel_after_first_page)
    service = _service(jobs, drafts, store, FakeExtractor([_text_page(1), _text_page(2)]), llm)

    await service.run(job_id)

    job = await jobs.get(job_id)
    assert job is not None and job.status is JobStatus.CANCELLED
    assert [r.source_page for r in await drafts.list_for_job(job_id)] == [1]


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


async def test_progress_is_committed_as_each_page_finishes() -> None:
    jobs, drafts, store = (
        InMemoryAiJobRepository(), InMemoryDraftCardRepository(), InMemoryObjectStore()
    )
    uow = RecordingUnitOfWork(jobs)
    pages = [_text_page(1), _text_page(2)]
    service = _service(jobs, drafts, store, FakeExtractor(pages), FakeLLM(), uow)
    job_id = await _seed_job(jobs, store)

    await service.run(job_id)

    # Claimed, sized, then each page committed with its drafts; the final state is
    # committed by the unit of work that runs the worker.
    assert uow.log == [
        "commit running:0/0", "commit running:0/2", "commit running:1/2", "commit running:2/2"
    ]
    assert len(await drafts.list_for_job(job_id)) == 2


async def test_a_failed_page_is_rolled_back_before_the_job_fails() -> None:
    jobs, drafts, store = (
        InMemoryAiJobRepository(), InMemoryDraftCardRepository(), InMemoryObjectStore()
    )
    uow = RecordingUnitOfWork(jobs)
    pages = [_text_page(1), _text_page(2)]
    service = _service(jobs, drafts, store, FakeExtractor(pages), FakeLLM(fail_on_call=2), uow)
    job_id = await _seed_job(jobs, store)

    await service.run(job_id)

    assert uow.log[-1] == "rollback running:1/2"  # page 2's partial work is discarded
    job = await jobs.get(job_id)
    assert job is not None and job.status is JobStatus.FAILED and job.completed_units == 1


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (LlmError("sk-secret rate limited by upstream"), JobFailure.PROVIDER_UNAVAILABLE),
        (InvalidModelOutput("raw model text"), JobFailure.BAD_MODEL_OUTPUT),
        (UnreadableDocument("FileDataError"), JobFailure.UNREADABLE_DOCUMENT),
        (UnsupportedMediaType("image/gif"), JobFailure.UNREADABLE_DOCUMENT),
        (TooManyPages("99 pages"), JobFailure.UNREADABLE_DOCUMENT),
        (InvalidTranscript("bad"), JobFailure.UNREADABLE_DOCUMENT),
        (QuotaExceeded("over"), JobFailure.QUOTA_EXCEEDED),
        (AiCallLimitReached("over"), JobFailure.QUOTA_EXCEEDED),
        (RuntimeError("postgres://user:pw@db/x is down"), JobFailure.INTERNAL),
    ],
)
def test_failures_are_classified_into_safe_messages(
    error: Exception, failure: JobFailure
) -> None:
    assert classify_failure(error) is failure


async def test_failure_detail_is_logged_not_stored(caplog: pytest.LogCaptureFixture) -> None:
    jobs, drafts, store = (
        InMemoryAiJobRepository(),
        InMemoryDraftCardRepository(),
        InMemoryObjectStore(),
    )
    secret = "provider said: key sk-123 invalid"
    service = _service(
        jobs, drafts, store, FakeExtractor([_text_page(1)]), FakeLLM(error=LlmError(secret))
    )
    job_id = await _seed_job(jobs, store)

    with caplog.at_level(logging.WARNING, logger="flashcards"):
        await service.run(job_id)

    job = await jobs.get(job_id)
    assert job is not None and secret not in (job.error or "")
    assert any(secret in record.getMessage() for record in caplog.records)


async def test_unexpected_failure_is_logged_with_its_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    jobs, drafts = InMemoryAiJobRepository(), InMemoryDraftCardRepository()
    service = _service(jobs, drafts, InMemoryObjectStore(), FakeExtractor([]), FakeLLM())
    job = new_generation_job(
        owner_id=ALICE,
        source_id="missing",
        draft_deck_id="deck",
        thoroughness=Thoroughness.CONCISE,
        now=NOW,
    )
    await jobs.add(job)

    with caplog.at_level(logging.ERROR, logger="flashcards"):
        await service.run(job.id)

    assert [r.exc_info is not None for r in caplog.records] == [True]
