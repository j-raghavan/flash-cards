# SPDX-License-Identifier: AGPL-3.0-only
"""Computing the study plan for a goal from its decks' live cards (F-PLAN-2/3/4).

The plan is always recomputed from the current cards and schedules, so it adapts
by itself after missed days or new cards. To show what changed, the plan the user
last accepted is kept as a baseline: the first plan computed for a goal becomes
its baseline, and accepting the current plan replaces it.
"""

from __future__ import annotations

from dataclasses import dataclass

from flashcards.application.goal_service import GoalService
from flashcards.application.progress_service import ProgressService
from flashcards.domain.goals import Goal
from flashcards.domain.planner import (
    PlanBaseline,
    PlanChange,
    StudyPlan,
    build_study_plan,
    plan_change,
)
from flashcards.domain.ports import (
    CardRepository,
    Clock,
    PlanBaselineRepository,
    ScheduleRepository,
)


@dataclass(frozen=True, slots=True)
class PlanReport:
    plan: StudyPlan
    change: PlanChange | None  # None until there is an earlier plan to compare with


class PlannerService:
    def __init__(
        self,
        goals: GoalService,
        cards: CardRepository,
        schedules: ScheduleRepository,
        baselines: PlanBaselineRepository,
        progress: ProgressService,
        clock: Clock,
    ) -> None:
        self._goals = goals
        self._cards = cards
        self._schedules = schedules
        self._baselines = baselines
        self._progress = progress
        self._clock = clock

    async def plan_for(self, *, owner_id: str, goal_id: str) -> StudyPlan:
        goal = await self._goals.get(owner_id=owner_id, goal_id=goal_id)
        return await self._compute(goal)

    async def report(self, *, owner_id: str, goal_id: str) -> PlanReport:
        """The current plan and how it differs from the accepted baseline."""
        goal = await self._goals.get(owner_id=owner_id, goal_id=goal_id)
        plan = await self._compute(goal)
        baseline = await self._baselines.get(goal.id)
        if baseline is None:
            await self._baselines.add_if_absent(self._baseline(goal, plan))
            return PlanReport(plan=plan, change=None)
        missed = await self._progress.missed_days_since(
            owner_id=owner_id, deck_ids=goal.deck_ids, since=baseline.taken_at
        )
        change = plan_change(
            baseline,
            current=plan,
            exam_date=goal.exam_date,
            target_retention=goal.target_retention,
            missed=missed,
        )
        return PlanReport(plan=plan, change=change)

    async def accept(self, *, owner_id: str, goal_id: str) -> StudyPlan:
        """Make the current plan the baseline later changes are measured from."""
        goal = await self._goals.get(owner_id=owner_id, goal_id=goal_id)
        plan = await self._compute(goal)
        await self._baselines.save(self._baseline(goal, plan))
        return plan

    def _baseline(self, goal: Goal, plan: StudyPlan) -> PlanBaseline:
        return PlanBaseline(
            goal_id=goal.id,
            owner_id=goal.owner_id,
            taken_at=self._clock.now(),
            exam_date=goal.exam_date,
            target_retention=goal.target_retention,
            plan=plan,
        )

    async def _compute(self, goal: Goal) -> StudyPlan:
        new_count = 0
        stabilities: list[float] = []
        for deck_id in goal.deck_ids:
            cards = [c for c in await self._cards.list_for_deck(deck_id) if not c.is_suspended]
            schedules = await self._schedules.get_many([card.id for card in cards])
            for card in cards:
                schedule = schedules.get(card.id)
                if schedule is None or schedule.stability is None:
                    new_count += 1  # never reviewed yet
                else:
                    stabilities.append(schedule.stability)
        return build_study_plan(
            exam_date=goal.exam_date,
            now=self._clock.now(),
            target_retention=goal.target_retention,
            new_count=new_count,
            stabilities=stabilities,
        )
