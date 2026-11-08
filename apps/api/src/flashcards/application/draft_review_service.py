# SPDX-License-Identifier: AGPL-3.0-only
"""Reviewing and approving generated draft cards (F-AI-4).

Generated cards land as drafts and are never scheduled until the user approves
them here, at which point they become real notes (and cards) in the target deck.
"""

from __future__ import annotations

from flashcards.application.note_service import NoteService
from flashcards.domain.ai import DraftCardRecord, DraftNotFound
from flashcards.domain.jobs import AiJob, JobNotFound
from flashcards.domain.notes import Card
from flashcards.domain.ports import AiJobRepository, DraftCardRepository


class DraftReviewService:
    def __init__(
        self, jobs: AiJobRepository, drafts: DraftCardRepository, notes: NoteService
    ) -> None:
        self._jobs = jobs
        self._drafts = drafts
        self._notes = notes

    async def list_drafts(self, *, owner_id: str, job_id: str) -> list[DraftCardRecord]:
        await self._require_job(owner_id, job_id)
        return await self._drafts.list_for_job(job_id)

    async def approve(
        self, *, owner_id: str, job_id: str, draft_ids: list[str]
    ) -> list[Card]:
        """Promote the chosen drafts into real notes in the job's deck, then remove them."""
        job = await self._require_job(owner_id, job_id)
        # Resolve every draft first, so one bad id approves nothing (all or nothing).
        resolved = [await self._require_draft(owner_id, job_id, draft_id) for draft_id in draft_ids]

        created: list[Card] = []
        for draft in resolved:
            _note, cards = await self._notes.create_note(
                owner_id=owner_id,
                deck_id=job.draft_deck_id,
                note_type=draft.note_type,
                fields=dict(draft.fields),
            )
            created.extend(cards)
            await self._drafts.delete(draft.id)
        return created

    async def _require_job(self, owner_id: str, job_id: str) -> AiJob:
        job = await self._jobs.get(job_id)
        if job is None or job.owner_id != owner_id:
            raise JobNotFound(job_id)
        return job

    async def _require_draft(
        self, owner_id: str, job_id: str, draft_id: str
    ) -> DraftCardRecord:
        draft = await self._drafts.get(draft_id)
        if draft is None or draft.owner_id != owner_id or draft.job_id != job_id:
            raise DraftNotFound(draft_id)
        return draft
