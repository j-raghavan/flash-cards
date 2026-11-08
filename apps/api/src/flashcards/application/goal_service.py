# SPDX-License-Identifier: AGPL-3.0-only
"""Creating and managing exam goals (F-PLAN-1).

Every deck a goal names is verified to be the caller's, so a goal can never span
another user's decks, and reads are owner-scoped so a goal that is not yours is
indistinguishable from one that does not exist.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from flashcards.application.deck_service import DeckService
from flashcards.domain.goals import Goal, GoalNotFound, new_goal
from flashcards.domain.ports import Clock, GoalRepository


class GoalService:
    def __init__(self, decks: DeckService, goals: GoalRepository, clock: Clock) -> None:
        self._decks = decks
        self._goals = goals
        self._clock = clock

    async def create(
        self,
        *,
        owner_id: str,
        name: str,
        deck_ids: Sequence[str],
        exam_date: datetime,
        desired_grade: float,
    ) -> Goal:
        await self._verify_decks(owner_id, deck_ids)
        goal = new_goal(
            owner_id=owner_id,
            name=name,
            deck_ids=deck_ids,
            exam_date=exam_date,
            desired_grade=desired_grade,
            now=self._clock.now(),
        )
        await self._goals.add(goal)
        return goal

    async def get(self, *, owner_id: str, goal_id: str) -> Goal:
        goal = await self._goals.get(goal_id)
        if goal is None or goal.owner_id != owner_id:
            raise GoalNotFound(goal_id)
        return goal

    async def list_goals(self, *, owner_id: str) -> list[Goal]:
        return await self._goals.list_for_owner(owner_id)

    async def update(
        self,
        *,
        owner_id: str,
        goal_id: str,
        name: str | None = None,
        deck_ids: Sequence[str] | None = None,
        exam_date: datetime | None = None,
        desired_grade: float | None = None,
    ) -> Goal:
        goal = await self.get(owner_id=owner_id, goal_id=goal_id)
        if name is not None:
            goal = goal.renamed(name)
        if deck_ids is not None:
            await self._verify_decks(owner_id, deck_ids)
            goal = goal.with_decks(deck_ids)
        if exam_date is not None:
            goal = goal.rescheduled(exam_date, self._clock.now())
        if desired_grade is not None:
            goal = goal.with_grade(desired_grade)
        await self._goals.update(goal)
        return goal

    async def delete(self, *, owner_id: str, goal_id: str) -> None:
        await self.get(owner_id=owner_id, goal_id=goal_id)  # ownership check
        await self._goals.delete(goal_id)

    async def _verify_decks(self, owner_id: str, deck_ids: Sequence[str]) -> None:
        for deck_id in deck_ids:
            await self._decks.get(owner_id=owner_id, deck_id=deck_id)  # raises DeckNotFound
