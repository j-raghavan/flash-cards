# SPDX-License-Identifier: AGPL-3.0-only
"""Goal and study-plan routes (F-PLAN-1..4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, current_user, get_container
from flashcards.api.schemas import (
    GoalCreateRequest,
    GoalOut,
    GoalUpdateRequest,
    StudyPlanOut,
)

router = APIRouter(prefix="/goals", tags=["goals"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=GoalOut)
async def create_goal(
    body: GoalCreateRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> GoalOut:
    goal = await container.goal_service.create(
        owner_id=caller.user.id,
        name=body.name,
        deck_ids=body.deck_ids,
        exam_date=body.exam_date,
        desired_grade=body.desired_grade,
    )
    return GoalOut.from_goal(goal)


@router.get("", response_model=list[GoalOut])
async def list_goals(
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> list[GoalOut]:
    goals = await container.goal_service.list_goals(owner_id=caller.user.id)
    return [GoalOut.from_goal(goal) for goal in goals]


@router.get("/{goal_id}", response_model=GoalOut)
async def get_goal(
    goal_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> GoalOut:
    return GoalOut.from_goal(
        await container.goal_service.get(owner_id=caller.user.id, goal_id=goal_id)
    )


@router.patch("/{goal_id}", response_model=GoalOut)
async def update_goal(
    goal_id: str,
    body: GoalUpdateRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> GoalOut:
    goal = await container.goal_service.update(
        owner_id=caller.user.id,
        goal_id=goal_id,
        name=body.name,
        deck_ids=body.deck_ids,
        exam_date=body.exam_date,
        desired_grade=body.desired_grade,
    )
    return GoalOut.from_goal(goal)


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(
    goal_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    await container.goal_service.delete(owner_id=caller.user.id, goal_id=goal_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{goal_id}/plan", response_model=StudyPlanOut)
async def goal_plan(
    goal_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> StudyPlanOut:
    plan = await container.planner.plan_for(owner_id=caller.user.id, goal_id=goal_id)
    return StudyPlanOut.from_plan(plan)
