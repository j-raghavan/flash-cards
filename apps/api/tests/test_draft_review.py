# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from flashcards.adapters.memory import (
    InMemoryAiJobRepository,
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryDraftCardRepository,
    InMemoryNoteRepository,
)
from flashcards.application.deck_service import DeckService
from flashcards.application.draft_review_service import DraftReviewService
from flashcards.application.note_service import NoteService
from flashcards.domain.ai import DraftCardRecord, DraftNotFound, Thoroughness
from flashcards.domain.jobs import AiJob, JobNotFound, new_generation_job
from flashcards.domain.notes import Fields, NoteType

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
ALICE = "alice"
BOB = "bob"


class FakeClock:
    def now(self) -> datetime:
        return NOW


class _Fixture:
    def __init__(self) -> None:
        clock = FakeClock()
        self.decks = DeckService(InMemoryDeckRepository(), clock)
        self.notes = NoteService(
            self.decks, InMemoryNoteRepository(), InMemoryCardRepository(), clock
        )
        self.jobs = InMemoryAiJobRepository()
        self.drafts = InMemoryDraftCardRepository()
        self.service = DraftReviewService(self.jobs, self.drafts, self.notes)

    async def job_with_drafts(self, owner: str = ALICE) -> AiJob:
        deck = await self.decks.create(owner_id=owner, name="Deck")
        job = new_generation_job(
            owner_id=owner,
            source_id="src",
            draft_deck_id=deck.id,
            thoroughness=Thoroughness.STANDARD,
            now=NOW,
        )
        await self.jobs.add(job)
        await self._draft(job, NoteType.BASIC, (("front", "Q"), ("back", "A")), page=1)
        await self._draft(job, NoteType.CLOZE, (("text", "{{c1::a}} {{c2::b}}"),), page=2)
        return job

    async def _draft(self, job: AiJob, note_type: NoteType, fields: Fields, page: int) -> str:
        record = DraftCardRecord(
            id=str(uuid.uuid4()),
            job_id=job.id,
            owner_id=job.owner_id,
            note_type=note_type,
            fields=fields,
            source_page=page,
            created_at=NOW,
        )
        await self.drafts.add(record)
        return record.id


@pytest.fixture
def fx() -> _Fixture:
    return _Fixture()


async def test_list_drafts_is_owner_scoped(fx: _Fixture) -> None:
    job = await fx.job_with_drafts()
    assert len(await fx.service.list_drafts(owner_id=ALICE, job_id=job.id)) == 2
    with pytest.raises(JobNotFound):
        await fx.service.list_drafts(owner_id=BOB, job_id=job.id)


async def test_approve_promotes_drafts_to_cards(fx: _Fixture) -> None:
    job = await fx.job_with_drafts()
    drafts = await fx.service.list_drafts(owner_id=ALICE, job_id=job.id)

    created = await fx.service.approve(
        owner_id=ALICE, job_id=job.id, draft_ids=[d.id for d in drafts]
    )

    # Basic -> 1 card, Cloze (c1,c2) -> 2 cards.
    assert len(created) == 3
    assert len(await fx.notes.list_cards(owner_id=ALICE, deck_id=job.draft_deck_id)) == 3
    # Approved drafts are consumed.
    assert await fx.service.list_drafts(owner_id=ALICE, job_id=job.id) == []


async def test_approve_a_subset_keeps_the_rest(fx: _Fixture) -> None:
    job = await fx.job_with_drafts()
    drafts = await fx.service.list_drafts(owner_id=ALICE, job_id=job.id)
    await fx.service.approve(owner_id=ALICE, job_id=job.id, draft_ids=[drafts[0].id])
    remaining = await fx.service.list_drafts(owner_id=ALICE, job_id=job.id)
    assert [d.id for d in remaining] == [drafts[1].id]


async def test_delete_for_job_clears_all_drafts(fx: _Fixture) -> None:
    job = await fx.job_with_drafts()
    await fx.drafts.delete_for_job(job.id)
    assert await fx.drafts.list_for_job(job.id) == []


async def test_approve_foreign_job_is_not_found(fx: _Fixture) -> None:
    job = await fx.job_with_drafts()
    with pytest.raises(JobNotFound):
        await fx.service.approve(owner_id=BOB, job_id=job.id, draft_ids=[])


async def test_approve_is_all_or_nothing_on_a_bad_id(fx: _Fixture) -> None:
    job = await fx.job_with_drafts()
    drafts = await fx.service.list_drafts(owner_id=ALICE, job_id=job.id)
    with pytest.raises(DraftNotFound):
        await fx.service.approve(
            owner_id=ALICE, job_id=job.id, draft_ids=[drafts[0].id, "does-not-exist"]
        )
    # Nothing was created and no draft was consumed.
    assert len(await fx.service.list_drafts(owner_id=ALICE, job_id=job.id)) == 2
    assert await fx.notes.list_cards(owner_id=ALICE, deck_id=job.draft_deck_id) == []
