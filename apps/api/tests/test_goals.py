# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.domain.goals import (
    Goal,
    InvalidGoal,
    new_goal,
    target_retention_for_grade,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
EXAM = NOW + timedelta(days=30)


def _goal(**overrides: object) -> Goal:
    kwargs: dict[str, object] = {
        "owner_id": "alice",
        "name": "Biology final",
        "deck_ids": ["d1", "d2"],
        "exam_date": EXAM,
        "desired_grade": 0.5,
        "now": NOW,
    }
    kwargs.update(overrides)
    return new_goal(**kwargs)  # type: ignore[arg-type]


def test_new_goal_sets_fields_and_maps_grade() -> None:
    goal = _goal()
    assert goal.owner_id == "alice"
    assert goal.name == "Biology final"
    assert goal.deck_ids == ("d1", "d2")
    assert goal.exam_date == EXAM
    assert goal.created_at == NOW
    assert goal.id
    assert goal.target_retention == target_retention_for_grade(0.5)


def test_grade_maps_onto_the_retention_band() -> None:
    assert target_retention_for_grade(0.0) == 0.80
    assert target_retention_for_grade(1.0) == 0.97
    # A middle grade lands between the bounds.
    assert 0.80 < target_retention_for_grade(0.5) < 0.97


def test_duplicate_decks_are_collapsed_in_order() -> None:
    goal = _goal(deck_ids=["d1", "d2", "d1", "d3"])
    assert goal.deck_ids == ("d1", "d2", "d3")


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_invalid_name_is_rejected(name: str) -> None:
    with pytest.raises(InvalidGoal):
        _goal(name=name)


def test_a_goal_needs_at_least_one_deck() -> None:
    with pytest.raises(InvalidGoal):
        _goal(deck_ids=[])


@pytest.mark.parametrize("exam", [NOW, NOW - timedelta(days=1)])
def test_exam_date_must_be_in_the_future(exam: datetime) -> None:
    with pytest.raises(InvalidGoal):
        _goal(exam_date=exam)


@pytest.mark.parametrize("grade", [-0.1, 1.1])
def test_desired_grade_must_be_a_fraction(grade: float) -> None:
    with pytest.raises(InvalidGoal):
        _goal(desired_grade=grade)


def test_renamed_validates() -> None:
    goal = _goal()
    assert goal.renamed("Chemistry").name == "Chemistry"
    with pytest.raises(InvalidGoal):
        goal.renamed("  ")


def test_rescheduled_requires_a_future_date() -> None:
    goal = _goal()
    later = EXAM + timedelta(days=10)
    assert goal.rescheduled(later, NOW).exam_date == later
    with pytest.raises(InvalidGoal):
        goal.rescheduled(NOW, NOW)


def test_with_grade_and_with_decks() -> None:
    goal = _goal()
    assert goal.with_grade(1.0).target_retention == 0.97
    assert goal.with_decks(["d9", "d9", "d8"]).deck_ids == ("d9", "d8")
    with pytest.raises(InvalidGoal):
        goal.with_decks([])
