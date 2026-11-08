# SPDX-License-Identifier: AGPL-3.0-only
"""FSRS implementation of the Scheduler port (F-SRS-1).

Translates between the domain CardSchedule and the fsrs library's Card so the
domain never imports fsrs. Fuzzing is off by default for deterministic output.
"""

from __future__ import annotations

from datetime import datetime

from fsrs import Card as FsrsCard
from fsrs import Rating as FsrsRating
from fsrs import Scheduler as FsrsEngine
from fsrs import State as FsrsState

from flashcards.domain.scheduling import CardSchedule, Rating, ScheduleState

_TO_DOMAIN_STATE = {
    FsrsState.Learning: ScheduleState.LEARNING,
    FsrsState.Review: ScheduleState.REVIEW,
    FsrsState.Relearning: ScheduleState.RELEARNING,
}
_FROM_DOMAIN_STATE = {domain: fsrs for fsrs, domain in _TO_DOMAIN_STATE.items()}

DEFAULT_RETENTION = 0.9


class FsrsScheduler:
    def __init__(
        self, desired_retention: float = DEFAULT_RETENTION, enable_fuzzing: bool = False
    ) -> None:
        self._engine = FsrsEngine(
            desired_retention=desired_retention, enable_fuzzing=enable_fuzzing
        )

    def new_schedule(self, card_id: str, now: datetime) -> CardSchedule:
        return self._to_domain(card_id, FsrsCard(due=now, last_review=None))

    def review(self, schedule: CardSchedule, rating: Rating, now: datetime) -> CardSchedule:
        reviewed, _log = self._engine.review_card(
            self._from_domain(schedule), FsrsRating(int(rating)), now
        )
        return self._to_domain(schedule.card_id, reviewed)

    def _to_domain(self, card_id: str, card: FsrsCard) -> CardSchedule:
        return CardSchedule(
            card_id=card_id,
            state=_TO_DOMAIN_STATE[card.state],
            step=card.step,
            stability=card.stability,
            difficulty=card.difficulty,
            due_at=card.due,
            last_reviewed_at=card.last_review,
        )

    def _from_domain(self, schedule: CardSchedule) -> FsrsCard:
        return FsrsCard(
            state=_FROM_DOMAIN_STATE[schedule.state],
            step=schedule.step,
            stability=schedule.stability,
            difficulty=schedule.difficulty,
            due=schedule.due_at,
            last_review=schedule.last_reviewed_at,
        )
