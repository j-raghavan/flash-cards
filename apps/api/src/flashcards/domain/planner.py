# SPDX-License-Identifier: AGPL-3.0-only
"""The study plan: daily load and exam-day readiness for a goal (F-PLAN-2, F-PLAN-4).

Pure given the goal's target, the exam date, and the goal's current cards (how
many are still new, and the stabilities of the ones already being reviewed).
Recomputing it from the live card state is exactly what makes the plan adapt
after missed days or new cards (F-PLAN-3); a saved baseline lets the user see
what changed since they last accepted the plan, and why.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from flashcards.domain.scheduling import interval_for_retention

# Policy ceilings for what a learner can sustain in a day; the readiness forecast
# degrades when a goal's workload runs past them in the time left.
DAILY_NEW_CAPACITY = 20
DAILY_REVIEW_CAPACITY = 200


@dataclass(frozen=True, slots=True)
class StudyPlan:
    days_until_exam: int
    total_cards: int
    new_cards: int
    scheduled_cards: int
    new_per_day: int
    reviews_per_day: int
    readiness: float  # predicted exam-day retention, 0..1 (F-PLAN-4)
    on_track: bool


def build_study_plan(
    *,
    exam_date: datetime,
    now: datetime,
    target_retention: float,
    new_count: int,
    stabilities: Sequence[float],
) -> StudyPlan:
    days = max(0, (exam_date - now).days)
    scheduled = len(stabilities)
    total = new_count + scheduled

    # Introduce every new card by the exam; cram into one day if the exam is today.
    new_per_day = math.ceil(new_count / days) if days > 0 else new_count
    # Maintenance load: each reviewed card comes due about every interval days.
    review_load = sum(
        1.0 / interval_for_retention(stability, target_retention) for stability in stabilities
    )

    readiness = _readiness(
        days=days,
        new_count=new_count,
        scheduled=scheduled,
        total=total,
        target=target_retention,
        review_load=review_load,
    )
    return StudyPlan(
        days_until_exam=days,
        total_cards=total,
        new_cards=new_count,
        scheduled_cards=scheduled,
        new_per_day=new_per_day,
        reviews_per_day=round(review_load),
        readiness=readiness,
        on_track=readiness >= target_retention,
    )


def _readiness(
    *,
    days: int,
    new_count: int,
    scheduled: int,
    total: int,
    target: float,
    review_load: float,
) -> float:
    """Expected exam-day retention if the plan is followed.

    Reviewed cards are kept at the target (scaled down if the review load outruns
    daily capacity); new cards reach the target only if there is time to introduce
    them (scaled down, to zero when the exam is already here).
    """
    if total == 0:
        return 0.0
    if new_count == 0:
        new_feasible = 1.0
    elif days <= 0:
        new_feasible = 0.0
    else:
        new_feasible = min(1.0, (DAILY_NEW_CAPACITY * days) / new_count)
    review_feasible = 1.0 if review_load <= 0 else min(1.0, DAILY_REVIEW_CAPACITY / review_load)
    covered = scheduled * review_feasible + new_count * new_feasible
    return max(0.0, min(1.0, target * covered / total))


@dataclass(frozen=True, slots=True)
class PlanBaseline:
    """The plan the user last accepted for a goal, with the inputs it came from."""

    goal_id: str
    owner_id: str
    taken_at: datetime
    exam_date: datetime
    target_retention: float
    plan: StudyPlan


class ChangeReason(enum.StrEnum):
    CARDS_ADDED = "cards_added"  # net new cards in the goal's decks
    CARDS_REMOVED = "cards_removed"  # net cards deleted or suspended
    CARDS_STARTED = "cards_started"  # net new cards reviewed for the first time
    MISSED_DAYS = "missed_days"  # days since the baseline with no reviews
    DAYS_PASSED = "days_passed"  # fewer days left until the exam
    EXAM_DATE_CHANGED = "exam_date_changed"
    TARGET_CHANGED = "target_changed"  # the desired grade moved


@dataclass(frozen=True, slots=True)
class PlanChange:
    since: datetime
    before: StudyPlan
    after: StudyPlan
    reasons: tuple[tuple[ChangeReason, int], ...]  # (reason, how many; 0 if not a count)

    @property
    def daily_load_before(self) -> int:
        return self.before.new_per_day + self.before.reviews_per_day

    @property
    def daily_load_after(self) -> int:
        return self.after.new_per_day + self.after.reviews_per_day

    @property
    def changed(self) -> bool:
        return self.before != self.after


def plan_change(
    baseline: PlanBaseline,
    *,
    current: StudyPlan,
    exam_date: datetime,
    target_retention: float,
    missed: int,
) -> PlanChange:
    """Compare the current plan with the baseline and explain the difference."""
    before = baseline.plan
    reasons: list[tuple[ChangeReason, int]] = []
    if exam_date != baseline.exam_date:
        reasons.append((ChangeReason.EXAM_DATE_CHANGED, 0))
    if target_retention != baseline.target_retention:
        reasons.append((ChangeReason.TARGET_CHANGED, 0))
    net = current.total_cards - before.total_cards
    if net > 0:
        reasons.append((ChangeReason.CARDS_ADDED, net))
    elif net < 0:
        reasons.append((ChangeReason.CARDS_REMOVED, -net))
    # Net growth of the reviewed set: new cards studied for the first time.
    started = current.scheduled_cards - before.scheduled_cards
    if started > 0:
        reasons.append((ChangeReason.CARDS_STARTED, started))
    if missed > 0:
        reasons.append((ChangeReason.MISSED_DAYS, missed))
    passed = before.days_until_exam - current.days_until_exam
    if passed > 0 and exam_date == baseline.exam_date:
        reasons.append((ChangeReason.DAYS_PASSED, passed))
    return PlanChange(since=baseline.taken_at, before=before, after=current, reasons=tuple(reasons))
