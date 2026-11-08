# SPDX-License-Identifier: AGPL-3.0-only
"""The worker drains the queue and runs a generation job end to end (in-memory)."""

from __future__ import annotations

import pymupdf

from flashcards.api.provider import in_memory_provider
from flashcards.domain.ai import StructuredRequest, StructuredResult, Thoroughness, TokenUsage
from flashcards.domain.jobs import JobStatus


class FakeLLM:
    def __init__(self, cards: list[dict[str, object]]) -> None:
        self._cards = cards

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        return StructuredResult(data={"cards": self._cards}, usage=TokenUsage(1, 1), model="fake")


def _pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"page {i} content to read")
    try:
        return bytes(doc.tobytes())
    finally:
        doc.close()


async def test_worker_runs_a_job_to_success_and_produces_drafts() -> None:
    fake = FakeLLM([{"type": "basic", "fields": {"front": "Q", "back": "A"}}])
    container, _provider, queue = in_memory_provider(llm=fake)
    deck = await container.decks.create(owner_id="alice", name="Deck")

    job = await container.generation.start(
        owner_id="alice", deck_id=deck.id, data=_pdf(2), thoroughness=Thoroughness.STANDARD
    )

    queue.start(container.card_generation.run)
    await queue.join()
    await queue.stop()

    done = await container.generation.get(owner_id="alice", job_id=job.id)
    assert done.status is JobStatus.SUCCEEDED
    assert done.total_units == 2 and done.completed_units == 2
    drafts = await container.draft_review.list_drafts(owner_id="alice", job_id=job.id)
    assert [d.source_page for d in drafts] == [1, 2]  # one card per page


async def test_worker_marks_a_job_failed_when_the_llm_errors() -> None:
    fake = FakeLLM([{"type": "basic", "fields": {"front": 123}}])  # invalid draft -> InvalidDraft
    container, _provider, queue = in_memory_provider(llm=fake)
    deck = await container.decks.create(owner_id="alice", name="Deck")
    job = await container.generation.start(
        owner_id="alice", deck_id=deck.id, data=_pdf(1), thoroughness=Thoroughness.STANDARD
    )

    queue.start(container.card_generation.run)
    await queue.join()
    await queue.stop()

    done = await container.generation.get(owner_id="alice", job_id=job.id)
    assert done.status is JobStatus.FAILED
    assert done.error is not None
