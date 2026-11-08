# SPDX-License-Identifier: AGPL-3.0-only
"""Scheduling value objects: ratings, schedule state and the review log.

The algorithm lives behind the Scheduler port (see ports.py); these types carry
the state an FSRS-style scheduler needs without the domain importing any
scheduler library, so the algorithm stays swappable (F-SRS-1).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime


class Rating(enum.IntEnum):
    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4


class ScheduleState(enum.StrEnum):
    LEARNING = "learning"
    REVIEW = "review"
    RELEARNING = "relearning"


@dataclass(frozen=True, slots=True)
class CardSchedule:
    card_id: str
    state: ScheduleState
    step: int | None
    stability: float | None
    difficulty: float | None
    due_at: datetime
    last_reviewed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReviewLog:
    id: str
    owner_id: str
    card_id: str
    rating: Rating
    reviewed_at: datetime
    idempotency_key: str | None = None
