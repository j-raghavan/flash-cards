# SPDX-License-Identifier: AGPL-3.0-only
"""Mapping between Anki's SM-2 fields and our FSRS schedule (F-IMP-2, F-IMP-4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.domain.anki import (
    AnkiCard,
    AnkiNoteType,
    AnkiReview,
    deck_path,
    has_dropped_fields,
    in_selected_decks,
    our_ordinal,
)
from flashcards.domain.anki_schedule import (
    DEFAULT_FACTOR,
    MAX_FACTOR,
    MIN_FACTOR,
    QUEUE_LEARNING,
    QUEUE_NEW,
    QUEUE_REVIEW,
    QUEUE_SUSPENDED,
    TYPE_LEARNING,
    TYPE_NEW,
    TYPE_RELEARNING,
    TYPE_REVIEW,
    day_zero,
    difficulty_from_factor,
    factor_from_difficulty,
    rating_for,
    review_time,
    revlog_kinds,
    schedule_from_sm2,
    sm2_from_schedule,
)
from flashcards.domain.scheduling import CardSchedule, Rating, ScheduleState

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
CRT = int(datetime(2026, 1, 1, 9, 30, tzinfo=UTC).timestamp())
BASIC = AnkiNoteType(id=1, name="Basic", is_cloze=False, field_names=("Front", "Back"))
CLOZE = AnkiNoteType(id=2, name="Cloze", is_cloze=True, field_names=("Text", "Extra"))


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


@pytest.mark.parametrize(
    ("ease", "kind", "expected"),
    [
        (1, 0, Rating.AGAIN),
        (2, 1, Rating.HARD),
        (3, 2, Rating.GOOD),
        (4, 3, Rating.EASY),
        (0, 1, None),  # no button: a manual change
        (5, 1, None),
        (3, 4, None),  # manual
        (3, 5, None),  # rescheduled
    ],
)
def test_rating_for_reads_only_real_answers(ease: int, kind: int, expected: Rating | None) -> None:
    assert rating_for(AnkiReview(card_id=1, reviewed_at_ms=0, ease=ease, kind=kind)) == expected


def test_review_time_is_bounded_to_anki_s_lifetime() -> None:
    def at(ms: int) -> datetime | None:
        return review_time(AnkiReview(1, ms, 3, 1), NOW)

    assert at(_ms(NOW - timedelta(days=3))) == NOW - timedelta(days=3)
    assert at(_ms(datetime(2005, 12, 31, tzinfo=UTC))) is None
    assert at(_ms(NOW + timedelta(seconds=1))) is None
    assert at(2**62) is None  # far beyond any real date, without overflowing


def test_factor_and_difficulty_are_inverse_and_clamped() -> None:
    assert difficulty_from_factor(DEFAULT_FACTOR) == 5.0
    assert difficulty_from_factor(0) == 5.0  # a new card has no ease yet
    assert difficulty_from_factor(MIN_FACTOR) == 10.0
    assert difficulty_from_factor(9000) == 1.0
    assert factor_from_difficulty(5.0) == DEFAULT_FACTOR
    assert factor_from_difficulty(None) == DEFAULT_FACTOR
    assert factor_from_difficulty(10.0) == MIN_FACTOR
    assert factor_from_difficulty(-50.0) == MAX_FACTOR
    assert factor_from_difficulty(difficulty_from_factor(2140)) == 2140


def test_day_zero_is_midnight_utc_of_a_plausible_creation_time() -> None:
    assert day_zero(CRT) == datetime(2026, 1, 1, tzinfo=UTC)
    assert day_zero(0) is None
    assert day_zero(10**12) is None


def _review_card(**overrides: int) -> AnkiCard:
    values = {"card_type": TYPE_REVIEW, "queue": 2, "due": 300, "interval": 20, "factor": 2500}
    values.update(overrides)
    return AnkiCard("g", 1, 0, id=5, **values)


def test_sm2_review_card_gets_a_conservative_schedule() -> None:
    # Due on day 300 after 2026-01-01 is 2026-10-28: already overdue, so kept as is.
    schedule = schedule_from_sm2("c1", _review_card(), CRT, NOW)
    assert schedule is not None
    assert schedule.state is ScheduleState.REVIEW
    assert schedule.stability == 20.0 and schedule.difficulty == 5.0
    assert schedule.due_at == datetime(2026, 10, 28, tzinfo=UTC)
    assert schedule.last_reviewed_at == datetime(2026, 10, 8, tzinfo=UTC)


def test_sm2_due_is_never_later_than_one_interval_from_now() -> None:
    schedule = schedule_from_sm2("c1", _review_card(due=5000), CRT, NOW)
    assert schedule is not None
    assert schedule.due_at == NOW + timedelta(days=20)
    assert schedule.last_reviewed_at == NOW


@pytest.mark.parametrize(
    ("card", "created_at_s"),
    [(_review_card(), None), (_review_card(due=10**9), CRT), (_review_card(), 5)],
)
def test_sm2_without_a_trustworthy_due_day_is_due_now(
    card: AnkiCard, created_at_s: int | None
) -> None:
    schedule = schedule_from_sm2("c1", card, created_at_s, NOW)
    assert schedule is not None and schedule.due_at == NOW


def test_sm2_card_in_a_filtered_deck_uses_its_original_due() -> None:
    # In a filtered deck `due` is a queue position; the home deck's due is `odue`.
    card = _review_card(due=-100_000, original_deck_id=7, original_due=300)
    schedule = schedule_from_sm2("c1", card, CRT, NOW)
    assert schedule is not None and schedule.due_at == datetime(2026, 10, 28, tzinfo=UTC)


def test_sm2_never_dates_a_review_before_anki_existed() -> None:
    schedule = schedule_from_sm2("c1", _review_card(due=-99_999, interval=30), CRT, NOW)
    assert schedule is not None
    assert schedule.last_reviewed_at == datetime(2006, 1, 1, tzinfo=UTC)


def test_revlog_kinds_follow_learning_review_and_relearning() -> None:
    again, hard, good, easy = Rating.AGAIN, Rating.HARD, Rating.GOOD, Rating.EASY
    assert revlog_kinds([]) == []
    assert revlog_kinds([again, hard, good, hard, again, again, easy, good]) == [
        0, 0, 0, 1, 1, 2, 2, 1,
    ]


def test_sm2_caps_a_huge_interval() -> None:
    schedule = schedule_from_sm2("c1", _review_card(interval=10**9), None, NOW)
    assert schedule is not None and schedule.stability == 36_500.0


@pytest.mark.parametrize(
    "card",
    [
        _review_card(card_type=TYPE_NEW),
        _review_card(card_type=TYPE_LEARNING),
        _review_card(card_type=TYPE_RELEARNING),
        _review_card(interval=0),
        _review_card(interval=-600),  # a v1 learning interval in seconds
    ],
)
def test_sm2_gives_no_schedule_to_anything_but_a_graduated_card(card: AnkiCard) -> None:
    assert schedule_from_sm2("c1", card, CRT, NOW) is None


ORIGIN = datetime(2026, 11, 1, tzinfo=UTC)


_LAST = datetime(2026, 11, 8, 8, tzinfo=UTC)


def _schedule(
    state: ScheduleState,
    *,
    stability: float | None = 12.4,
    difficulty: float | None = 5.0,
    last_reviewed_at: datetime | None = _LAST,
) -> CardSchedule:
    return CardSchedule(
        card_id="c1",
        state=state,
        step=None,
        stability=stability,
        difficulty=difficulty,
        due_at=datetime(2026, 11, 20, 8, tzinfo=UTC),
        last_reviewed_at=last_reviewed_at,
    )


def test_new_card_exports_its_queue_position() -> None:
    fields = sm2_from_schedule(None, suspended=False, new_position=7, origin=ORIGIN, ratings=[])
    assert (fields.card_type, fields.queue) == (TYPE_NEW, QUEUE_NEW)
    assert (fields.due, fields.interval) == (7, 0)
    suspended = sm2_from_schedule(None, suspended=True, new_position=0, origin=ORIGIN, ratings=[])
    assert suspended.queue == QUEUE_SUSPENDED


def test_review_card_exports_day_number_interval_and_ease() -> None:
    ratings = [Rating.GOOD, Rating.AGAIN, Rating.AGAIN, Rating.GOOD]
    fields = sm2_from_schedule(
        _schedule(ScheduleState.REVIEW, difficulty=6.0),
        suspended=False,
        new_position=0,
        origin=ORIGIN,
        ratings=ratings,
    )
    assert fields.card_type == TYPE_REVIEW and fields.queue == QUEUE_REVIEW
    assert fields.due == 19 and fields.interval == 12
    assert fields.factor == 2260
    assert (fields.reps, fields.lapses) == (4, 2)


def test_review_card_without_a_last_review_uses_stability() -> None:
    fields = sm2_from_schedule(
        _schedule(ScheduleState.REVIEW, last_reviewed_at=None),
        suspended=True,
        new_position=0,
        origin=ORIGIN,
        ratings=[Rating.AGAIN],  # never passed, so not a lapse
    )
    assert fields.interval == 12 and fields.queue == QUEUE_SUSPENDED and fields.lapses == 0
    no_stability = sm2_from_schedule(
        _schedule(ScheduleState.REVIEW, last_reviewed_at=None, stability=None),
        suspended=False,
        new_position=0,
        origin=ORIGIN,
        ratings=[],
    )
    assert no_stability.interval == 1


def test_learning_and_relearning_cards_export_a_timestamp_due() -> None:
    learning = sm2_from_schedule(
        _schedule(ScheduleState.LEARNING, stability=None, difficulty=None),
        suspended=False,
        new_position=0,
        origin=ORIGIN,
        ratings=[Rating.GOOD],
    )
    assert learning.card_type == TYPE_LEARNING and learning.queue == QUEUE_LEARNING
    assert learning.due == int(datetime(2026, 11, 20, 8, tzinfo=UTC).timestamp())
    assert learning.interval == 0 and learning.factor == DEFAULT_FACTOR
    assert learning.left == 1001  # one step left, so Anki keeps it learning

    relearning = sm2_from_schedule(
        _schedule(ScheduleState.RELEARNING),
        suspended=True,
        new_position=0,
        origin=ORIGIN,
        ratings=[],
    )
    assert relearning.card_type == TYPE_RELEARNING and relearning.queue == QUEUE_SUSPENDED
    assert relearning.interval == 12
    unknown = sm2_from_schedule(
        _schedule(ScheduleState.RELEARNING, stability=None),
        suspended=False,
        new_position=0,
        origin=ORIGIN,
        ratings=[],
    )
    assert unknown.interval == 1


def test_note_mapping_helpers() -> None:
    assert has_dropped_fields(BASIC, ("Q", "A", "  ")) is False
    assert has_dropped_fields(BASIC, ("Q", "A", "source")) is True
    assert has_dropped_fields(CLOZE, ("{{c1::x}}", "extra")) is True
    assert our_ordinal(BASIC, 0) == 0 and our_ordinal(BASIC, 1) is None
    assert our_ordinal(CLOZE, 0) == 1 and our_ordinal(CLOZE, 4) == 5
    assert our_ordinal(CLOZE, -1) is None
    assert deck_path("Languages::Spanish") == ("Languages", "Spanish")


def test_deck_selection_includes_sub_decks_ignoring_case() -> None:
    assert in_selected_decks("Anything", ())
    assert in_selected_decks("Languages::Spanish", ("languages",))
    assert in_selected_decks(" Languages ", ("Languages",))
    assert not in_selected_decks("Languages2", ("Languages",))
    assert not in_selected_decks("Biology", ("Languages", "Chemistry"))
