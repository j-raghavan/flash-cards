# SPDX-License-Identifier: AGPL-3.0-only
"""Computing the study plan for a goal from its decks' live cards (F-PLAN-2/3/4)."""

from __future__ import annotations

from flashcards.application.goal_service import GoalService
from flashcards.domain.planner import StudyPlan, build_study_plan
from flashcards.domain.ports import CardRepository, Clock, ScheduleRepository


class PlannerService:
    def __init__(
        self,
        goals: GoalService,
        cards: CardRepository,
        schedules: ScheduleRepository,
        clock: Clock,
    ) -> None:
        self._goals = goals
        self._cards = cards
        self._schedules = schedules
        self._clock = clock

    async def plan_for(self, *, owner_id: str, goal_id: str) -> StudyPlan:
        goal = await self._goals.get(owner_id=owner_id, goal_id=goal_id)
        new_count = 0
        stabilities: list[float] = []
        for deck_id in goal.deck_ids:
            for card in await self._cards.list_for_deck(deck_id):
                if card.is_suspended:
                    continue  # suspended cards are not part of the plan
                schedule = await self._schedules.get(card.id)
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
