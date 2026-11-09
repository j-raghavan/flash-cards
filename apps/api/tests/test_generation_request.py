# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import (
    InMemoryAiJobRepository,
    InMemoryAuditRepository,
    InMemoryDeckRepository,
    InMemoryJobQueue,
    InMemoryObjectStore,
    InMemoryQuotaRepository,
    InMemoryQuotaSettingsRepository,
    InMemorySpendHoldRepository,
    InMemoryUsageRepository,
    InMemoryUserRepository,
)
from flashcards.application.ai_spend import AiSpend
from flashcards.application.audit_service import AuditService
from flashcards.application.deck_service import DeckService
from flashcards.application.generation_request_service import (
    DEFAULT_JOB_PAGE,
    MAX_JOB_PAGE,
    GenerationRequestService,
)
from flashcards.application.quota_service import QuotaService
from flashcards.domain.ai import Thoroughness
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.jobs import JobNotFound, JobStatus, new_generation_job
from flashcards.domain.quotas import InstanceQuotas, QuotaExceeded
from flashcards.domain.sources import (
    MAX_SOURCE_NAME,
    ExtractedPage,
    TooManyPages,
    UnsupportedMediaType,
    clean_source_name,
    source_name_key,
)
from flashcards.domain.transcripts import Transcript, TranscriptSegment, pasted_transcript
from flashcards.domain.usage import TokenPrices

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
ALICE = "alice"
BOB = "bob"


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakeExtractor:
    def __init__(
        self,
        *,
        media: str = "application/pdf",
        pages: int = 2,
        sniff_error: Exception | None = None,
    ) -> None:
        self._media = media
        self._pages = pages
        self._sniff_error = sniff_error

    def sniff(self, data: bytes) -> str:
        if self._sniff_error is not None:
            raise self._sniff_error
        return self._media

    async def count_pages(self, data: bytes) -> int:
        return self._pages

    async def extract(
        self, data: bytes, *, file_id: str, max_pages: int
    ) -> AsyncIterator[ExtractedPage]:
        raise NotImplementedError  # pragma: no cover
        yield  # pragma: no cover


def _limits_and_spend(clock: FakeClock, quota_limit: int = 10) -> tuple[QuotaService, AiSpend]:
    usage = InMemoryUsageRepository()
    limits = QuotaService(
        InMemoryQuotaSettingsRepository(),
        InMemoryQuotaRepository(),
        usage,
        InMemoryUserRepository(),
        AuditService(InMemoryAuditRepository(), clock),
        clock,
        defaults=InstanceQuotas(default_monthly_pages=quota_limit),
    )
    spend = AiSpend(
        usage,
        InMemoryQuotaSettingsRepository(),
        clock,
        InMemorySpendHoldRepository(usage),
        provider="test",
        prices=TokenPrices(),
        defaults=InstanceQuotas(),
    )
    return limits, spend


def _service(
    extractor: FakeExtractor,
    *,
    quota_limit: int = 10,
) -> tuple[GenerationRequestService, DeckService, InMemoryJobQueue, InMemoryObjectStore]:
    clock = FakeClock()
    decks = DeckService(InMemoryDeckRepository(), clock)
    queue = InMemoryJobQueue()
    store = InMemoryObjectStore()
    usage = InMemoryUsageRepository()
    service = GenerationRequestService(
        decks,
        extractor,
        store,
        InMemoryQuotaRepository(limit=quota_limit),
        InMemoryAiJobRepository(),
        queue,
        clock,
        QuotaService(
            InMemoryQuotaSettingsRepository(),
            InMemoryQuotaRepository(),
            usage,
            InMemoryUserRepository(),
            AuditService(InMemoryAuditRepository(), clock),
            clock,
            defaults=InstanceQuotas(default_monthly_pages=quota_limit),
        ),
        AiSpend(
            usage,
            InMemoryQuotaSettingsRepository(),
            clock,
            InMemorySpendHoldRepository(usage),
            provider="test",
            prices=TokenPrices(),
            defaults=InstanceQuotas(),
        ),
    )
    return service, decks, queue, store


async def test_start_stores_file_reserves_quota_and_enqueues() -> None:
    service, decks, queue, store = _service(FakeExtractor(pages=3))
    deck = await decks.create(owner_id=ALICE, name="Deck")

    job = await service.start(
        owner_id=ALICE, deck_id=deck.id, data=b"%PDF", thoroughness=Thoroughness.STANDARD
    )

    assert job.status is JobStatus.QUEUED
    assert job.draft_deck_id == deck.id
    assert queue.enqueued == [job.id]
    assert await store.get(job.source_id) == b"%PDF"
    # No name was given, so none is kept.
    assert await store.get(source_name_key(job.source_id)) is None


async def test_start_keeps_the_cleaned_file_name_beside_the_source() -> None:
    service, decks, _queue, store = _service(FakeExtractor())
    deck = await decks.create(owner_id=ALICE, name="Deck")

    job = await service.start(
        owner_id=ALICE,
        deck_id=deck.id,
        data=b"%PDF",
        thoroughness=Thoroughness.STANDARD,
        source_name="C:\\Users\\a\\Cell\u202e  biology.pdf",
    )

    assert await store.get(source_name_key(job.source_id)) == b"Cell biology.pdf"


async def test_transcripts_are_named_by_their_kind() -> None:
    service, decks, _queue, store = _service(FakeExtractor())
    deck = await decks.create(owner_id=ALICE, name="Deck")
    video = Transcript(video_id="abc123", segments=(TranscriptSegment(start=0.0, text="Hi"),))

    from_video = await service.start_transcript(
        owner_id=ALICE, deck_id=deck.id, transcript=video, thoroughness=Thoroughness.STANDARD
    )
    pasted = await service.start_transcript(
        owner_id=ALICE,
        deck_id=deck.id,
        transcript=pasted_transcript("Hello"),
        thoroughness=Thoroughness.STANDARD,
    )

    assert await store.get(source_name_key(from_video.source_id)) == b"YouTube video abc123"
    assert await store.get(source_name_key(pasted.source_id)) == b"Pasted transcript"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("notes.pdf", "notes.pdf"),
        ("a/b/c.pdf", "c.pdf"),
        ("dir\\x.png", "x.png"),
        ("tab\there\nand\x00 nul", "tab here and nul"),
        ("   ", None),
        ("folder/", None),
        ("x" * 500, "x" * MAX_SOURCE_NAME),
    ],
)
def test_clean_source_name(raw: str | None, expected: str | None) -> None:
    assert clean_source_name(raw) == expected


async def test_start_on_foreign_deck_is_not_found() -> None:
    service, decks, _queue, _store = _service(FakeExtractor())
    bob_deck = await decks.create(owner_id=BOB, name="Bob's")
    with pytest.raises(DeckNotFound):
        await service.start(
            owner_id=ALICE, deck_id=bob_deck.id, data=b"%PDF", thoroughness=Thoroughness.STANDARD
        )


async def test_start_rejects_unsupported_media() -> None:
    service, decks, _queue, _store = _service(
        FakeExtractor(sniff_error=UnsupportedMediaType("image/gif"))
    )
    deck = await decks.create(owner_id=ALICE, name="Deck")
    with pytest.raises(UnsupportedMediaType):
        await service.start(
            owner_id=ALICE, deck_id=deck.id, data=b"GIF89a", thoroughness=Thoroughness.STANDARD
        )


async def test_start_rejects_too_many_pages() -> None:
    service, decks, _queue, _store = _service(FakeExtractor(pages=999))
    deck = await decks.create(owner_id=ALICE, name="Deck")
    with pytest.raises(TooManyPages):
        await service.start(
            owner_id=ALICE, deck_id=deck.id, data=b"%PDF", thoroughness=Thoroughness.STANDARD
        )


async def test_start_rejects_when_over_quota() -> None:
    service, decks, queue, _store = _service(FakeExtractor(pages=5), quota_limit=3)
    deck = await decks.create(owner_id=ALICE, name="Deck")
    with pytest.raises(QuotaExceeded):
        await service.start(
            owner_id=ALICE, deck_id=deck.id, data=b"%PDF", thoroughness=Thoroughness.STANDARD
        )
    assert queue.enqueued == []  # nothing enqueued when quota is refused


async def test_get_and_cancel_are_owner_scoped() -> None:
    service, decks, _queue, _store = _service(FakeExtractor())
    deck = await decks.create(owner_id=ALICE, name="Deck")
    job = await service.start(
        owner_id=ALICE, deck_id=deck.id, data=b"%PDF", thoroughness=Thoroughness.CONCISE
    )

    assert (await service.get(owner_id=ALICE, job_id=job.id)).id == job.id
    with pytest.raises(JobNotFound):
        await service.get(owner_id=BOB, job_id=job.id)
    with pytest.raises(JobNotFound):
        await service.cancel(owner_id=BOB, job_id=job.id)

    await service.cancel(owner_id=ALICE, job_id=job.id)
    assert (await service.get(owner_id=ALICE, job_id=job.id)).cancel_requested is True


async def test_list_recent_is_owner_scoped_newest_first_and_bounded() -> None:
    jobs = InMemoryAiJobRepository()
    clock = FakeClock()
    service = GenerationRequestService(
        DeckService(InMemoryDeckRepository(), clock),
        FakeExtractor(),
        InMemoryObjectStore(),
        InMemoryQuotaRepository(),
        jobs,
        InMemoryJobQueue(),
        clock,
        *_limits_and_spend(clock),
    )
    created: list[str] = []
    for minute in range(MAX_JOB_PAGE + 5):
        job = new_generation_job(
            owner_id=ALICE,
            source_id=f"src-{minute}",
            draft_deck_id="d1" if minute % 2 == 0 else "d2",
            thoroughness=Thoroughness.STANDARD,
            now=NOW + timedelta(minutes=minute),
        )
        await jobs.add(job)
        created.append(job.id)
    await jobs.add(
        new_generation_job(
            owner_id=BOB,
            source_id="bob",
            draft_deck_id="d1",
            thoroughness=Thoroughness.STANDARD,
            now=NOW + timedelta(days=1),
        )
    )
    newest_first = list(reversed(created))

    page = await service.list_recent(owner_id=ALICE)
    assert [j.id for j in page] == newest_first[:DEFAULT_JOB_PAGE]
    second = await service.list_recent(owner_id=ALICE, limit=3, offset=3)
    assert [j.id for j in second] == newest_first[3:6]
    # Out-of-range values are clamped rather than trusted.
    assert len(await service.list_recent(owner_id=ALICE, limit=1000)) == MAX_JOB_PAGE
    clamped = await service.list_recent(owner_id=ALICE, limit=0, offset=-4)
    assert [j.id for j in clamped] == newest_first[:1]
    d2 = await service.list_recent(owner_id=ALICE, deck_id="d2", limit=MAX_JOB_PAGE)
    assert d2 and all(j.draft_deck_id == "d2" for j in d2)
    assert await service.list_recent(owner_id="nobody") == []
