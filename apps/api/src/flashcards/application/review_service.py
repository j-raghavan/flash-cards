# SPDX-License-Identifier: AGPL-3.0-only
"""Review use cases: submit a grade and fetch the due queue (F-SRS-2, F-STUDY-1).

Review logs are the append-only source of truth; the card schedule is a
projection the scheduler advances on each review. Submissions are idempotent on
a client-supplied key so a retry never double-counts a review (NF-REL-1).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from flashcards.application.note_service import NoteService
from flashcards.domain.notes import Card
from flashcards.domain.ports import (
    Clock,
    ReviewLogRepository,
    Scheduler,
    ScheduleRepository,
)
from flashcards.domain.scheduling import CardSchedule, Rating, ReviewLog


class ReviewService:
    def __init__(
        self,
        notes: NoteService,
        schedules: ScheduleRepository,
        logs: ReviewLogRepository,
        scheduler: Scheduler,
        clock: Clock,
    ) -> None:
        self._notes = notes
        self._schedules = schedules
        self._logs = logs
        self._scheduler = scheduler
        self._clock = clock

    async def submit(
        self,
        *,
        owner_id: str,
        card_id: str,
        rating: Rating,
        idempotency_key: str | None = None,
    ) -> CardSchedule:
        # Raises CardNotFound if the card is missing or not the caller's.
        await self._notes.get_card(owner_id=owner_id, card_id=card_id)
        now = self._clock.now()

        if idempotency_key is not None and await self._logs.exists_key(owner_id, idempotency_key):
            return await self._current_schedule(card_id, now)

        current = await self._current_schedule(card_id, now)
        updated = self._scheduler.review(current, rating, now)
        await self._logs.append(
            ReviewLog(
                id=str(uuid.uuid4()),
                owner_id=owner_id,
                card_id=card_id,
                rating=rating,
                reviewed_at=now,
                idempotency_key=idempotency_key,
            )
        )
        await self._schedules.upsert(updated)
        return updated

    async def due_queue(self, *, owner_id: str, deck_id: str, limit: int = 50) -> list[Card]:
        # Raises DeckNotFound if the deck is missing or not the caller's.
        cards = await self._notes.list_cards(owner_id=owner_id, deck_id=deck_id)
        now = self._clock.now()
        due: list[Card] = []
        for card in cards:
            if card.is_suspended:
                continue
            schedule = await self._schedules.get(card.id)
            if schedule is None or schedule.due_at <= now:
                due.append(card)
        return due[:limit]

    async def _current_schedule(self, card_id: str, now: datetime) -> CardSchedule:
        existing = await self._schedules.get(card_id)
        if existing is not None:
            return existing
        return self._scheduler.new_schedule(card_id, now)
