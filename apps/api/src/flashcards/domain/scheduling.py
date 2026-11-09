# SPDX-License-Identifier: AGPL-3.0-only
"""Scheduling value objects: ratings, schedule state and the review log.

The algorithm lives behind the Scheduler port (see ports.py); these types carry
the state an FSRS-style scheduler needs without the domain importing any
scheduler library, so the algorithm stays swappable (F-SRS-1).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta


class DuplicateReview(Exception):
    """A review already exists for this (owner, card, idempotency key).

    Raised by ReviewLogRepository.append so idempotency is enforced atomically by
    the storage layer, not by a check-then-act in the service (NF-REL-1).
    """


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


MATURE_DAYS = 21

# FSRS v6 forgetting curve. The constants match the fsrs library's default
# parameters (parameters[20] = 0.1542), so a retention forecast computed here
# agrees with how the Scheduler port actually schedules cards. A scheduler
# configured with custom parameters would need the decay that matches it.
FSRS_DECAY = -0.1542
_FORGET_FACTOR: float = 0.9 ** (1 / FSRS_DECAY) - 1


def retrievability(stability: float, elapsed_days: float) -> float:
    """Predicted probability of recall after elapsed_days, given a memory's stability.

    The FSRS v6 forgetting curve R(t) = (1 + FACTOR * t / S) ** DECAY, clamped to
    [0, 1]. Before the memory is due (t <= 0) retention is full.
    """
    if stability <= 0:
        raise ValueError("stability must be positive")
    if elapsed_days <= 0:
        return 1.0
    curve = float((1 + _FORGET_FACTOR * elapsed_days / stability) ** FSRS_DECAY)
    return max(0.0, min(1.0, curve))


def interval_for_retention(stability: float, target_retention: float) -> float:
    """Days until retrievability decays to target_retention (the review interval).

    The inverse of retrievability: t = (S / FACTOR) * (target ** (1 / DECAY) - 1).
    """
    if stability <= 0:
        raise ValueError("stability must be positive")
    if not 0.0 < target_retention < 1.0:
        raise ValueError("target_retention must be in (0, 1)")
    return float((stability / _FORGET_FACTOR) * (target_retention ** (1 / FSRS_DECAY) - 1))


@dataclass(frozen=True, slots=True)
class DeckStats:
    total: int
    new: int
    learning: int
    review: int
    mature: int
    suspended: int
    due: int
    proficiency: int  # percent of non-suspended cards that have graduated (review + mature)


@dataclass(frozen=True, slots=True)
class UndoSnapshot:
    """The card's schedule just before a review, kept on its log so the answer can
    be taken back (F-STUDY-3). schedule is None when the card was new."""

    schedule: CardSchedule | None


@dataclass(frozen=True, slots=True)
class ReviewLog:
    id: str
    owner_id: str
    card_id: str
    rating: Rating
    reviewed_at: datetime
    idempotency_key: str | None = None
    # None for a review that cannot be undone (one recorded before undo existed).
    undo: UndoSnapshot | None = None


# How long after answering a card the answer can still be taken back.
UNDO_WINDOW = timedelta(minutes=10)


class ReviewNotFound(Exception):
    """A review that does not exist (or was already undone) or is not the caller's."""


class UndoRejected(Exception):
    """The review exists but cannot be undone; str(exc) says why."""
