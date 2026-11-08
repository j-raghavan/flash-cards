# SPDX-License-Identifier: AGPL-3.0-only
"""The domain forgetting-curve math (F-PLAN-2/4), cross-checked against fsrs."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from fsrs import Card as FsrsCard
from fsrs import Scheduler as FsrsEngine

from flashcards.domain.scheduling import (
    FSRS_DECAY,
    interval_for_retention,
    retrievability,
)

STABILITIES = [0.5, 1.0, 3.0, 10.0, 50.0, 365.0]
TARGETS = [0.70, 0.80, 0.90, 0.95, 0.99]


def test_matches_the_fsrs_library_forgetting_curve() -> None:
    # The whole point: a forecast here must agree with how the scheduler schedules.
    engine = FsrsEngine(enable_fuzzing=False)
    last = datetime(2026, 1, 1, tzinfo=UTC)
    for stability in STABILITIES:
        card = FsrsCard(stability=stability, difficulty=5.0, last_review=last, due=last)
        for days in (1, 3, 7, 30, 120, 500):
            expected = engine.get_card_retrievability(card, last + timedelta(days=days))
            assert math.isclose(retrievability(stability, days), expected, rel_tol=1e-9)


def test_full_retention_before_due() -> None:
    assert retrievability(10.0, 0) == 1.0
    assert retrievability(10.0, -5) == 1.0


def test_retrievability_is_a_decaying_probability() -> None:
    for stability in STABILITIES:
        previous = 1.0
        for days in range(1, 400):
            r = retrievability(stability, days)
            assert 0.0 <= r <= 1.0
            assert r <= previous  # monotonically non-increasing in elapsed time
            previous = r


def test_interval_round_trips_through_retrievability() -> None:
    # Decaying for exactly interval_for_retention(S, target) days lands on target.
    for stability in STABILITIES:
        for target in TARGETS:
            days = interval_for_retention(stability, target)
            assert days > 0
            assert math.isclose(retrievability(stability, days), target, rel_tol=1e-9)


def test_interval_grows_with_stability_and_shrinks_with_a_stricter_target() -> None:
    assert interval_for_retention(5.0, 0.9) < interval_for_retention(50.0, 0.9)
    # A higher retention target means reviewing sooner, so a shorter interval.
    assert interval_for_retention(10.0, 0.95) < interval_for_retention(10.0, 0.80)


def test_decay_constant_matches_fsrs_default() -> None:
    assert FSRS_DECAY == -0.1542


@pytest.mark.parametrize("bad_stability", [0.0, -1.0])
def test_non_positive_stability_is_rejected(bad_stability: float) -> None:
    with pytest.raises(ValueError, match="stability"):
        retrievability(bad_stability, 1.0)
    with pytest.raises(ValueError, match="stability"):
        interval_for_retention(bad_stability, 0.9)


@pytest.mark.parametrize("bad_target", [0.0, 1.0, -0.1, 1.5])
def test_target_retention_must_be_a_probability(bad_target: float) -> None:
    with pytest.raises(ValueError, match="target_retention"):
        interval_for_retention(10.0, bad_target)
