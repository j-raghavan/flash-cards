# SPDX-License-Identifier: AGPL-3.0-only
"""Mapping scheduling state between Anki's SM-2 fields and our FSRS schedule (ADR 0014).

Importing (F-IMP-2): a card's review history is the authority. Each revlog
answer becomes a Rating and is replayed through our Scheduler, so the schedule is
exactly what our own reviews would have produced. Only when a card has no usable
history do its SM-2 columns seed a schedule, and then conservatively: only a
graduated review card gets one (stability = its interval, difficulty from its
ease), and it is never due later than one interval from now. Learning cards
without history arrive as new.

Exporting (F-IMP-4): our schedule is written back as SM-2 columns Anki can read.
The two mappings are inverses on the default ease, so a round trip through our
own export keeps a card's difficulty.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from flashcards.domain.anki import AnkiCard, AnkiReview
from flashcards.domain.scheduling import CardSchedule, Rating, ScheduleState

ANKI_EPOCH_START = datetime(2006, 1, 1, tzinfo=UTC)  # nothing older is a real review
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Anki card types (cards.type) and queues (cards.queue).
TYPE_NEW, TYPE_LEARNING, TYPE_REVIEW, TYPE_RELEARNING = 0, 1, 2, 3
QUEUE_SUSPENDED = -1
QUEUE_NEW, QUEUE_LEARNING, QUEUE_REVIEW = 0, 1, 2
# Revlog kinds that record a real answer: learn, review, relearn and filtered.
# Manual changes (4) and reschedules (5) are not reviews.
_ANSWER_KINDS = frozenset({0, 1, 2, 3})

DEFAULT_FACTOR = 2500  # Anki's starting ease, 250%
MIN_FACTOR = 1300  # Anki's floor, 130%
MAX_FACTOR = 3700
_FACTOR_PER_DIFFICULTY = 240  # 1300..3700 spans FSRS difficulty 10..1 (inverted)
MIN_DIFFICULTY, MAX_DIFFICULTY = 1.0, 10.0
MIN_STABILITY = 0.1
MAX_INTERVAL_DAYS = 36_500  # a century; anything longer is treated as this
_MAX_DUE_DAYS = 100_000  # a review day number beyond this is not trusted


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def rating_for(review: AnkiReview) -> Rating | None:
    """The rating an Anki revlog row records, or None when it is not an answer.

    Ease 1 to 4 is the button pressed, as the FSRS optimizer reads it. Anki's old
    v1 scheduler offered three buttons while learning (3 was Easy); that reads as
    Good here, which can only underestimate the card, never overestimate it.
    """
    if review.kind not in _ANSWER_KINDS or not 1 <= review.ease <= 4:
        return None
    return Rating(review.ease)


def review_time(review: AnkiReview, now: datetime) -> datetime | None:
    """When the review happened, or None for a time before Anki existed or in the future."""
    earliest_ms = int((ANKI_EPOCH_START - _UNIX_EPOCH).total_seconds() * 1000)
    latest_ms = int((now - _UNIX_EPOCH).total_seconds() * 1000)
    if not earliest_ms <= review.reviewed_at_ms <= latest_ms:
        return None
    return _UNIX_EPOCH + timedelta(milliseconds=review.reviewed_at_ms)


def difficulty_from_factor(factor: int) -> float:
    """FSRS difficulty (1 easy to 10 hard) from an SM-2 ease in permille."""
    ease = factor if factor > 0 else DEFAULT_FACTOR
    difficulty = 5.0 + (DEFAULT_FACTOR - ease) / _FACTOR_PER_DIFFICULTY
    return _clamp(difficulty, MIN_DIFFICULTY, MAX_DIFFICULTY)


def factor_from_difficulty(difficulty: float | None) -> int:
    """The inverse of difficulty_from_factor, clamped to Anki's ease range."""
    if difficulty is None:
        return DEFAULT_FACTOR
    factor = round(DEFAULT_FACTOR - (difficulty - 5.0) * _FACTOR_PER_DIFFICULTY)
    return int(_clamp(factor, MIN_FACTOR, MAX_FACTOR))


def day_zero(created_at_s: int) -> datetime | None:
    """Midnight UTC of the collection's creation day, or None for an implausible value."""
    earliest_s = int((ANKI_EPOCH_START - _UNIX_EPOCH).total_seconds())
    latest_s = int((datetime(9000, 1, 1, tzinfo=UTC) - _UNIX_EPOCH).total_seconds())
    if not earliest_s <= created_at_s <= latest_s:
        return None
    created = _UNIX_EPOCH + timedelta(seconds=created_at_s)
    return created.replace(hour=0, minute=0, second=0, microsecond=0)


def schedule_from_sm2(
    card_id: str, card: AnkiCard, created_at_s: int | None, now: datetime
) -> CardSchedule | None:
    """A conservative FSRS schedule from a review card's SM-2 columns, or None.

    Only a graduated review card with a positive interval gets a schedule; every
    other card arrives as new. Stability is the interval in days (at our 90%
    target retention an FSRS interval roughly equals stability). The due date is
    the card's Anki due day when the collection's creation day is known, but
    never later than one interval from now; otherwise the card is due now.
    """
    if card.card_type != TYPE_REVIEW or card.interval <= 0:
        return None
    interval_days = min(card.interval, MAX_INTERVAL_DAYS)
    interval = timedelta(days=interval_days)
    origin = day_zero(created_at_s) if created_at_s is not None else None
    due_at = now
    if origin is not None and abs(card.due) <= _MAX_DUE_DAYS:
        due_at = min(origin + timedelta(days=card.due), now + interval)
    return CardSchedule(
        card_id=card_id,
        state=ScheduleState.REVIEW,
        step=None,
        stability=_clamp(float(interval_days), MIN_STABILITY, float(MAX_INTERVAL_DAYS)),
        difficulty=difficulty_from_factor(card.factor),
        due_at=due_at,
        last_reviewed_at=min(due_at - interval, now),
    )


@dataclass(frozen=True, slots=True)
class Sm2Fields:
    """The Anki cards columns a schedule is written back as."""

    card_type: int
    queue: int
    due: int
    interval: int
    factor: int
    reps: int
    lapses: int


def sm2_from_schedule(
    schedule: CardSchedule | None,
    *,
    suspended: bool,
    new_position: int,
    origin: datetime,
    ratings: Sequence[Rating],
) -> Sm2Fields:
    """Anki cards columns for one of our cards.

    `origin` is the exported collection's day zero (its crt, midnight UTC) and
    `ratings` the card's review history in order. A new card's due is its position
    in the new queue; a learning card's due is an epoch-seconds timestamp; a review
    card's due is a day number from `origin`. Lapses count Again answers after the
    card was first answered correctly.
    """
    reps = len(ratings)
    lapses = _lapses(ratings)
    if schedule is None:
        queue = QUEUE_SUSPENDED if suspended else QUEUE_NEW
        return Sm2Fields(TYPE_NEW, queue, new_position, 0, 0, reps, lapses)

    factor = factor_from_difficulty(schedule.difficulty)
    if schedule.state is ScheduleState.REVIEW:
        if schedule.last_reviewed_at is not None:
            elapsed = (schedule.due_at - schedule.last_reviewed_at) / timedelta(days=1)
        else:
            elapsed = schedule.stability or 1.0
        interval = max(1, min(MAX_INTERVAL_DAYS, round(elapsed)))
        due = (schedule.due_at.astimezone(UTC).date() - origin.date()).days
        queue = QUEUE_SUSPENDED if suspended else QUEUE_REVIEW
        return Sm2Fields(TYPE_REVIEW, queue, due, interval, factor, reps, lapses)

    learning = schedule.state is ScheduleState.LEARNING
    card_type = TYPE_LEARNING if learning else TYPE_RELEARNING
    interval = 0 if learning else max(1, min(MAX_INTERVAL_DAYS, round(schedule.stability or 1.0)))
    due = int((schedule.due_at - _UNIX_EPOCH).total_seconds())
    queue = QUEUE_SUSPENDED if suspended else QUEUE_LEARNING
    return Sm2Fields(card_type, queue, due, interval, factor, reps, lapses)


def _lapses(ratings: Sequence[Rating]) -> int:
    lapses = 0
    passed = False
    for rating in ratings:
        if rating is Rating.AGAIN:
            lapses += passed
        else:
            passed = True
    return lapses
