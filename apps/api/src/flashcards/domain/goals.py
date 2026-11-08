# SPDX-License-Identifier: AGPL-3.0-only
"""Goal entity and rules: an exam target that drives the study planner (F-PLAN-1).

A goal ties one or more decks to an exam date and a desired grade. The grade
slider maps onto the review retention target the planner aims for; everything
the planner needs beyond that it reads from the decks' live card schedules.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

MAX_GOAL_NAME = 200
# The desired-grade slider maps onto a review-retention target, bounded to a
# practical FSRS band: much lower barely schedules reviews, much higher schedules
# impractically often.
MIN_TARGET_RETENTION = 0.80
MAX_TARGET_RETENTION = 0.97


class GoalError(Exception):
    """Base class for goal failures."""


class InvalidGoal(GoalError):
    pass


class GoalNotFound(GoalError):
    """A goal that does not exist or is not the caller's."""


def validate_goal_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > MAX_GOAL_NAME:
        raise InvalidGoal(f"invalid goal name: {name!r}")
    return cleaned


def target_retention_for_grade(desired_grade: float) -> float:
    """Map a 0..1 desired-grade slider onto the planner's retention target."""
    if not 0.0 <= desired_grade <= 1.0:
        raise InvalidGoal("desired_grade must be in [0, 1]")
    span = MAX_TARGET_RETENTION - MIN_TARGET_RETENTION
    return round(MIN_TARGET_RETENTION + span * desired_grade, 4)


@dataclass(frozen=True, slots=True)
class Goal:
    id: str
    owner_id: str
    name: str
    deck_ids: tuple[str, ...]
    exam_date: datetime
    target_retention: float
    created_at: datetime

    def renamed(self, name: str) -> Goal:
        return replace(self, name=validate_goal_name(name))

    def rescheduled(self, exam_date: datetime, now: datetime) -> Goal:
        if exam_date <= now:
            raise InvalidGoal("exam_date must be in the future")
        return replace(self, exam_date=exam_date)

    def with_grade(self, desired_grade: float) -> Goal:
        return replace(self, target_retention=target_retention_for_grade(desired_grade))

    def with_decks(self, deck_ids: Sequence[str]) -> Goal:
        unique = _unique_decks(deck_ids)
        return replace(self, deck_ids=unique)


def _unique_decks(deck_ids: Sequence[str]) -> tuple[str, ...]:
    unique = tuple(dict.fromkeys(deck_ids))  # de-dup, preserving order
    if not unique:
        raise InvalidGoal("a goal needs at least one deck")
    return unique


def new_goal(
    *,
    owner_id: str,
    name: str,
    deck_ids: Sequence[str],
    exam_date: datetime,
    desired_grade: float,
    now: datetime,
) -> Goal:
    cleaned_name = validate_goal_name(name)
    unique_decks = _unique_decks(deck_ids)
    if exam_date <= now:
        raise InvalidGoal("exam_date must be in the future")
    return Goal(
        id=str(uuid.uuid4()),
        owner_id=owner_id,
        name=cleaned_name,
        deck_ids=unique_decks,
        exam_date=exam_date,
        target_retention=target_retention_for_grade(desired_grade),
        created_at=now,
    )
