# SPDX-License-Identifier: AGPL-3.0-only
"""Spend-cap holds: estimate before each AI call, settle or release after (ADR 0019)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from flashcards.adapters.memory import (
    InMemoryAiJobRepository,
    InMemoryDraftCardRepository,
    InMemoryObjectStore,
    InMemoryQuotaRepository,
    InMemoryQuotaSettingsRepository,
    InMemorySpendHoldRepository,
    InMemoryUsageRepository,
    NullUnitOfWork,
)
from flashcards.application.ai_budget import AiCallBudget
from flashcards.application.ai_spend import AiSpend
from flashcards.application.card_generation_service import CardGenerationService
from flashcards.domain.ai import (
    LlmDocument,
    LlmError,
    StructuredRequest,
    StructuredResult,
    Thoroughness,
    TokenUsage,
)
from flashcards.domain.jobs import JobFailure, JobStatus, new_generation_job
from flashcards.domain.quotas import InstanceQuotas
from flashcards.domain.sources import ExtractedPage, PageKind, SourceRef
from flashcards.domain.usage import (
    HOLD_TTL,
    NON_TEXT_DOCUMENT_TOKENS,
    AiFeature,
    SpendCapReached,
    SpendHold,
    TokenPrices,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
# One micro-USD per input token and per output token keeps the arithmetic visible.
PRICES = TokenPrices(input_usd_per_mtok=1.0, output_usd_per_mtok=1.0)


class _Clock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        return self.value


def _request(output: int = 100, text: bytes = b"") -> StructuredRequest:
    documents = (LlmDocument(media_type="text/plain", data=text),) if text else ()
    return StructuredRequest(
        instructions="", documents=documents, schema={}, max_output_tokens=output
    )


def _result(inp: int, out: int) -> StructuredResult:
    return StructuredResult(data={}, usage=TokenUsage(inp, out), model="m")


class _Rig:
    def __init__(self, cap: int | None) -> None:
        self.clock = _Clock()
        self.usage = InMemoryUsageRepository()
        self.holds = InMemorySpendHoldRepository(self.usage)
        self.spend = AiSpend(
            self.usage,
            InMemoryQuotaSettingsRepository(),
            self.clock,
            self.holds,
            provider="test",
            prices=PRICES,
            defaults=InstanceQuotas(monthly_spend_cap_micro_usd=cap),
        )

    async def held(self) -> int:
        return await self.holds.held("2026-11", self.clock.now())


def test_the_estimate_is_priced_at_the_output_cap_and_one_token_per_byte() -> None:
    request = StructuredRequest(
        instructions="abcd",  # 4 bytes
        documents=(
            LlmDocument(media_type="text/plain", data="é".encode()),  # 2 bytes
            LlmDocument(media_type="image/png", data=b"\x89PNG" * 1000),
        ),
        schema={},  # "{}": 2 bytes
        max_output_tokens=50,
    )
    assert PRICES.estimate_micro_usd(request) == 4 + 2 + 2 + NON_TEXT_DOCUMENT_TOKENS + 50
    assert TokenPrices().estimate_micro_usd(request) == 0


async def test_without_a_cap_nothing_is_held() -> None:
    rig = _Rig(cap=None)
    assert await rig.spend.hold(_request()) is None
    await rig.spend.release(None)
    await rig.spend.record("u1", AiFeature.CHAT, _result(1, 1), None)
    assert await rig.held() == 0


async def test_concurrent_calls_cannot_jointly_pass_the_cap_by_more_than_one_estimate() -> None:
    rig = _Rig(cap=250)
    holds = []
    with pytest.raises(SpendCapReached):
        for _ in range(10):  # ten calls start before any of them finishes
            holds.append(await rig.spend.hold(_request(output=100)))
    # Each estimate is 102 (100 output tokens, "{}" as the schema). 0, 102 and 204
    # are under the cap, so three holds; the fourth is refused.
    assert len(holds) == 3 and await rig.held() == 306
    assert await rig.held() < 250 + 102  # less than one estimate over the cap
    with pytest.raises(SpendCapReached):
        await rig.spend.ensure_available()

    # Each call settles its hold as it records its actual cost.
    for hold in holds:
        await rig.spend.record("u1", AiFeature.CHAT, _result(10, 20), hold)
    assert await rig.held() == 0
    assert await rig.usage.total_cost("2026-11") == 90
    await rig.spend.ensure_available()  # room again
    assert await rig.spend.hold(_request()) is not None


async def test_a_released_or_lapsed_hold_stops_counting() -> None:
    rig = _Rig(cap=150)
    first = await rig.spend.hold(_request(output=100))
    assert first is not None and first.expires_at == NOW + HOLD_TTL
    await rig.spend.hold(_request(output=100))
    with pytest.raises(SpendCapReached):
        await rig.spend.hold(_request(output=100))

    await rig.spend.release(first)
    assert await rig.held() == 102
    rig.clock.value = NOW + HOLD_TTL  # the other call's process died: its hold lapses
    assert await rig.held() == 0
    assert await rig.spend.hold(_request(output=100)) is not None


class _BrokenHolds(InMemorySpendHoldRepository):
    async def release(self, hold_id: str) -> None:
        raise ConnectionError("database unavailable")


async def test_a_failed_release_is_logged_and_the_hold_left_to_lapse(
    caplog: pytest.LogCaptureFixture,
) -> None:
    usage = InMemoryUsageRepository()
    spend = AiSpend(
        usage, InMemoryQuotaSettingsRepository(), _Clock(), _BrokenHolds(usage),
        provider="test", prices=PRICES, defaults=InstanceQuotas(),
    )
    hold = SpendHold(id="h", period="2026-11", amount_micro_usd=1, expires_at=NOW)
    with caplog.at_level(logging.WARNING, logger="flashcards"):
        await spend.release(hold)
    assert "could not release AI spend hold h" in caplog.text


async def test_the_budget_releases_the_hold_when_the_call_fails_or_is_refused() -> None:
    rig = _Rig(cap=1000)
    # In memory the failed call's count is not rolled back, so it uses one of two.
    budget = AiCallBudget(InMemoryQuotaRepository(limit=2), rig.clock, rig.spend, NullUnitOfWork())
    with pytest.raises(LlmError):
        async with budget.charge("u1", _request()) as hold:
            assert hold is not None and await rig.held() == 102
            raise LlmError("provider down")
    assert await rig.held() == 0

    async with budget.charge("u1", _request()) as hold:
        pass
    await budget.record("u1", AiFeature.GRADING, _result(5, 5), hold)
    # The daily allowance (2) is spent: the second hold is given back at once.
    with pytest.raises(Exception, match="u1"):
        async with budget.charge("u1", _request()):
            pass  # pragma: no cover - refused before the block runs
    assert await rig.held() == 0 and await rig.usage.total_cost("2026-11") == 10


class _Extractor:
    def sniff(self, data: bytes) -> str:  # pragma: no cover - not used by the worker
        return "application/pdf"

    async def count_pages(self, data: bytes) -> int:  # pragma: no cover
        return 2

    async def extract(
        self, data: bytes, *, file_id: str, max_pages: int
    ) -> AsyncIterator[ExtractedPage]:
        for page in (1, 2):
            yield ExtractedPage(
                ref=SourceRef(file_id=file_id, page=page), kind=PageKind.TEXT, text=f"page {page}"
            )


async def _queued_job(jobs: InMemoryAiJobRepository, store: InMemoryObjectStore) -> str:
    await store.put("src", b"%PDF-fake", "application/pdf")
    job = new_generation_job(
        owner_id="u1", source_id="src", draft_deck_id="d1",
        thoroughness=Thoroughness.STANDARD, now=NOW,
    )
    await jobs.add(job)
    return job.id


class _LLM:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        self.calls += 1
        return StructuredResult(data={"cards": []}, usage=TokenUsage(1, 1), model="m")


async def test_a_generation_job_stops_at_the_page_whose_hold_is_refused() -> None:
    rig = _Rig(cap=10)
    jobs, store, llm = InMemoryAiJobRepository(), InMemoryObjectStore(), _LLM()
    job_id = await _queued_job(jobs, store)
    # A hold of another call takes the room the first page would need.
    await rig.holds.try_hold(
        SpendHold(id="other", period="2026-11", amount_micro_usd=10, expires_at=NOW + HOLD_TTL),
        cap_micro_usd=100, now=NOW,
    )
    worker = CardGenerationService(
        jobs, InMemoryDraftCardRepository(), store, _Extractor(), llm, rig.clock,
        NullUnitOfWork(), rig.spend,
    )
    await worker.run(job_id)

    failed = await jobs.get(job_id)
    assert failed is not None and failed.status is JobStatus.FAILED
    assert failed.error == JobFailure.QUOTA_EXCEEDED.value
    assert llm.calls == 0


async def test_a_failed_page_call_releases_its_hold() -> None:
    rig = _Rig(cap=10_000)

    class _Failing(_LLM):
        async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
            raise LlmError("down")

    jobs, store = InMemoryAiJobRepository(), InMemoryObjectStore()
    job_id = await _queued_job(jobs, store)
    worker = CardGenerationService(
        jobs, InMemoryDraftCardRepository(), store, _Extractor(), _Failing(), rig.clock,
        NullUnitOfWork(), rig.spend,
    )
    await worker.run(job_id)
    job = await jobs.get(job_id)
    assert job is not None and job.status is JobStatus.FAILED
    assert await rig.held() == 0
