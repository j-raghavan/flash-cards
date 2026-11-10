# SPDX-License-Identifier: AGPL-3.0-only
"""Review use cases: submit a grade, undo it, and fetch the due queue
(F-SRS-2, F-STUDY-1, F-STUDY-3).

Review logs are the source of truth; the card schedule is a projection the
scheduler advances on each review. Submissions are idempotent on a
client-supplied key so a retry never double-counts a review (NF-REL-1).

Each log keeps the schedule the card had just before it, so the caller's most
recent answer can be taken back for a short while: the log is removed and the
schedule restored. Answers and undos of one owner are serialized (lock_owner),
so an undo can never interleave with a new answer.

Any card the caller may read can be studied, library cards included (ADR 0024).
The schedule read and written is always the caller's own, so a member's answers
never move the owner's progress. owner_id below is the reviewer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from flashcards.application.read_access import ReadAccess
from flashcards.domain.notes import Card
from flashcards.domain.ports import (
    Clock,
    ReviewLogRepository,
    Scheduler,
    ScheduleRepository,
)
from flashcards.domain.scheduling import (
    MATURE_DAYS,
    UNDO_WINDOW,
    CardSchedule,
    DeckStats,
    DuplicateReview,
    Rating,
    ReviewLog,
    ReviewNotFound,
    ScheduleState,
    UndoRejected,
    UndoSnapshot,
)


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    review_id: str
    schedule: CardSchedule


@dataclass(frozen=True, slots=True)
class UndoOutcome:
    review_id: str
    card_id: str
    schedule: CardSchedule | None  # the restored schedule; None: the card is new again


class ReviewService:
    def __init__(
        self,
        access: ReadAccess,
        schedules: ScheduleRepository,
        logs: ReviewLogRepository,
        scheduler: Scheduler,
        clock: Clock,
    ) -> None:
        self._access = access
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
    ) -> ReviewOutcome:
        await self._logs.lock_owner(owner_id)
        # Raises CardNotFound if the card is missing or not readable by the caller.
        # The card row stays locked against deletion until this answer commits, so
        # a card deleted meanwhile answers 404, never a foreign-key failure.
        await self._access.study_card(owner_id, card_id)
        now = self._clock.now()
        existing = await self._schedules.get(owner_id, card_id)
        current = existing or self._scheduler.new_schedule(card_id, now)
        updated = self._scheduler.review(current, rating, now)
        log = ReviewLog(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            card_id=card_id,
            rating=rating,
            reviewed_at=now,
            idempotency_key=idempotency_key,
            undo=UndoSnapshot(existing),
        )

        # An undone answer's log is gone, so its key no longer collides; a late
        # retry of that answer must not record it again behind the user's back.
        last = await self._logs.last_answer(owner_id)
        if (
            idempotency_key is not None
            and last is not None
            and last.undone
            and last.card_id == card_id
            and last.idempotency_key == idempotency_key
        ):
            raise UndoRejected("This answer was undone.")

        # Append first: the storage layer rejects a duplicate idempotency key
        # atomically, so the schedule is only advanced when a new review lands.
        try:
            await self._logs.append(log)
        except DuplicateReview:
            original = await self._logs.find_by_key(owner_id, card_id, idempotency_key or "")
            if original is None:  # cannot happen under the owner lock; never guess
                raise ReviewNotFound(card_id) from None
            stored = await self._schedules.get(owner_id, card_id)
            return ReviewOutcome(original.id, stored or current)

        await self._schedules.upsert(owner_id, updated)
        await self._logs.set_last_answer(log)
        return ReviewOutcome(log.id, updated)

    async def undo(self, *, owner_id: str, review_id: str) -> UndoOutcome:
        """Take back the caller's most recent answer, within UNDO_WINDOW.

        "Most recent" is a per-owner pointer set by every new answer, not the
        newest surviving log, so deleting a card never exposes an older answer.
        One step only: once undone, the pointer stays spent until the next answer.
        Repeating an undo changes nothing (ReviewNotFound).
        """
        await self._logs.lock_owner(owner_id)
        log = await self._logs.get(review_id)
        if log is None or log.owner_id != owner_id:
            raise ReviewNotFound(review_id)
        # The card must still be readable: a library deck whose admin is no longer
        # active, or a card its owner suspended, answers CardNotFound and nothing
        # changes. The row stays locked against deletion while the schedule moves.
        await self._access.study_card(owner_id, log.card_id)
        last = await self._logs.last_answer(owner_id)
        if last is None or last.review_id != log.id or last.undone:
            raise UndoRejected("Only the most recent answer can be undone.")
        if log.undo is None:
            raise UndoRejected("This answer cannot be undone.")
        if self._clock.now() - log.reviewed_at > UNDO_WINDOW:
            raise UndoRejected("The time to undo this answer has passed.")
        # Something other than this answer (a reset, an import) moved the card on.
        current = await self._schedules.get(owner_id, log.card_id)
        if current is None or current.last_reviewed_at != log.reviewed_at:
            raise UndoRejected("The card has changed since this answer.")
        if not await self._logs.try_delete(log.id):
            raise ReviewNotFound(review_id)
        await self._logs.mark_undone(owner_id)
        previous = log.undo.schedule
        if previous is None:
            await self._schedules.delete(owner_id, log.card_id)
        else:
            await self._schedules.upsert(owner_id, previous)
        return UndoOutcome(review_id=log.id, card_id=log.card_id, schedule=previous)

    async def due_queue(self, *, owner_id: str, deck_id: str, limit: int = 50) -> list[Card]:
        """The caller's due cards in a readable deck, as the caller sees them."""
        cards = await self._cards_seen_by(owner_id, deck_id)
        schedules = await self._schedules.get_many(owner_id, [card.id for card in cards])
        now = self._clock.now()
        due: list[Card] = []
        for card in cards:
            if card.is_suspended or card.is_buried(now):
                continue
            schedule = schedules.get(card.id)
            if schedule is None or schedule.due_at <= now:
                due.append(card)
        return due[:limit]

    async def deck_stats(self, *, owner_id: str, deck_id: str) -> DeckStats:
        """The caller's own progress over a readable deck."""
        cards = await self._cards_seen_by(owner_id, deck_id)
        schedules = await self._schedules.get_many(owner_id, [card.id for card in cards])
        now = self._clock.now()
        mature_after = timedelta(days=MATURE_DAYS)

        total = len(cards)
        new = learning = review = mature = suspended = due = 0
        for card in cards:
            if card.is_suspended:
                suspended += 1
                continue
            schedule = schedules.get(card.id)
            available = not card.is_buried(now)  # a buried card is not due until later
            if schedule is None:
                new += 1
                due += available
                continue
            if available and schedule.due_at <= now:
                due += 1
            if schedule.state in (ScheduleState.LEARNING, ScheduleState.RELEARNING):
                learning += 1
            elif schedule.last_reviewed_at is not None and (
                schedule.due_at - schedule.last_reviewed_at >= mature_after
            ):
                mature += 1
            else:
                review += 1

        active = total - suspended
        proficiency = round(100 * (review + mature) / active) if active else 0
        return DeckStats(
            total=total,
            new=new,
            learning=learning,
            review=review,
            mature=mature,
            suspended=suspended,
            due=due,
            proficiency=proficiency,
        )

    async def _cards_seen_by(self, reader_id: str, deck_id: str) -> list[Card]:
        """A readable deck's cards without the owner's bury and flag, unless the
        reader is the owner; raises DeckNotFound."""
        cards = await self._access.deck_cards(reader_id, deck_id)
        return [card.seen_by(reader_id) for card in cards]

