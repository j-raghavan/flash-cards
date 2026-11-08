# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from flashcards.domain.planner import (
    DAILY_NEW_CAPACITY,
    StudyPlan,
    build_study_plan,
)
from flashcards.domain.scheduling import interval_for_retention

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
TARGET = 0.9


def _plan(
    *,
    days: int,
    new_count: int = 0,
    stabilities: list[float] | None = None,
    target: float = TARGET,
) -> StudyPlan:
    return build_study_plan(
        exam_date=NOW + timedelta(days=days),
        now=NOW,
        target_retention=target,
        new_count=new_count,
        stabilities=stabilities or [],
    )


def test_empty_goal_has_no_load_and_no_readiness() -> None:
    plan = _plan(days=10)
    assert (plan.total_cards, plan.new_cards, plan.scheduled_cards) == (0, 0, 0)
    assert (plan.new_per_day, plan.reviews_per_day) == (0, 0)
    assert plan.readiness == 0.0
    assert plan.on_track is False


def test_new_cards_are_spread_over_the_days_and_reach_target() -> None:
    plan = _plan(days=10, new_count=5)
    assert plan.days_until_exam == 10
    assert plan.new_per_day == 1  # ceil(5 / 10)
    assert math.isclose(plan.readiness, TARGET)  # plenty of time to learn five cards
    assert plan.on_track is True


def test_exam_today_cannot_learn_new_material() -> None:
    plan = _plan(days=0, new_count=5)
    assert plan.new_per_day == 5  # all at once
    assert plan.readiness == 0.0
    assert plan.on_track is False


def test_too_many_new_cards_for_the_time_lowers_readiness() -> None:
    # Far more new cards than can be introduced in the days left.
    new = DAILY_NEW_CAPACITY * 10 * 5  # 5x the capacity over 10 days
    plan = _plan(days=10, new_count=new)
    assert plan.readiness < TARGET
    assert plan.on_track is False


def test_reviewed_cards_set_the_review_load_and_hold_target() -> None:
    stabilities = [10.0, 20.0, 40.0]
    plan = _plan(days=30, stabilities=stabilities)
    expected_load = sum(1.0 / interval_for_retention(s, TARGET) for s in stabilities)
    assert plan.reviews_per_day == round(expected_load)
    assert plan.scheduled_cards == 3
    assert math.isclose(plan.readiness, TARGET)
    assert plan.on_track is True


def test_a_crushing_review_load_lowers_readiness() -> None:
    # Many low-stability cards come due constantly, past the daily review capacity.
    plan = _plan(days=30, stabilities=[0.5] * 2000)
    assert plan.readiness < TARGET
    assert plan.on_track is False


def test_readiness_is_non_decreasing_in_available_days() -> None:
    previous = -1.0
    for days in range(0, 60, 5):
        plan = _plan(days=days, new_count=200, stabilities=[1.0] * 50)
        assert 0.0 <= plan.readiness <= 1.0
        assert plan.readiness >= previous  # more time never hurts the forecast
        previous = plan.readiness


def test_mixed_new_and_scheduled_blend_toward_total() -> None:
    plan = _plan(days=20, new_count=10, stabilities=[15.0] * 10)
    assert plan.total_cards == 20
    assert plan.new_cards == 10 and plan.scheduled_cards == 10
    assert 0.0 < plan.readiness <= TARGET
