# SPDX-License-Identifier: AGPL-3.0-only
"""The composition root wires the AI generation + review services (both modes)."""

from __future__ import annotations

import pymupdf

from flashcards.api.container import build_container
from flashcards.domain.ai import Thoroughness
from flashcards.domain.jobs import JobStatus


def _pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page()
    try:
        return bytes(doc.tobytes())
    finally:
        doc.close()


async def test_in_memory_container_starts_a_generation() -> None:
    container = build_container()
    deck = await container.decks.create(owner_id="alice", name="Deck")

    job = await container.generation.start(
        owner_id="alice", deck_id=deck.id, data=_pdf(), thoroughness=Thoroughness.STANDARD
    )

    assert job.status is JobStatus.QUEUED
    assert (await container.generation.get(owner_id="alice", job_id=job.id)).id == job.id
    # The draft-review service is wired over the same job repository.
    assert await container.draft_review.list_drafts(owner_id="alice", job_id=job.id) == []
