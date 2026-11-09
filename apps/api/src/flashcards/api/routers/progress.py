# SPDX-License-Identifier: AGPL-3.0-only
"""Progress routes: study settings, today's goal and streak, and statistics
(F-PROG-1, F-PROG-3)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, current_user, get_container
from flashcards.api.schemas import (
    ProgressStatsOut,
    StudySettingsOut,
    StudySettingsUpdateRequest,
    TodayOut,
)
from flashcards.domain.progress import (
    DEFAULT_FORECAST_DAYS,
    DEFAULT_HEATMAP_DAYS,
    MAX_FORECAST_DAYS,
    MAX_HEATMAP_DAYS,
)

router = APIRouter(prefix="/progress", tags=["progress"])


@router.get("/settings", response_model=StudySettingsOut)
async def get_settings(
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> StudySettingsOut:
    settings = await container.progress.get_settings(owner_id=caller.user.id)
    return StudySettingsOut.from_settings(settings)


@router.patch("/settings", response_model=StudySettingsOut)
async def update_settings(
    body: StudySettingsUpdateRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> StudySettingsOut:
    settings = await container.progress.update_settings(
        owner_id=caller.user.id, daily_goal=body.daily_goal, time_zone=body.time_zone
    )
    return StudySettingsOut.from_settings(settings)


@router.get("/today", response_model=TodayOut)
async def today(
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> TodayOut:
    progress = await container.progress.today(owner_id=caller.user.id)
    return TodayOut.from_today(progress)


@router.get("/stats", response_model=ProgressStatsOut)
async def stats(
    deck_id: str | None = None,
    days: int = Query(default=DEFAULT_HEATMAP_DAYS, ge=1, le=MAX_HEATMAP_DAYS),
    forecast_days: int = Query(default=DEFAULT_FORECAST_DAYS, ge=1, le=MAX_FORECAST_DAYS),
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> ProgressStatsOut:
    result = await container.progress.stats(
        owner_id=caller.user.id, deck_id=deck_id, days=days, forecast_days=forecast_days
    )
    return ProgressStatsOut.from_stats(result)
