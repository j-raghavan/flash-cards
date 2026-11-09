# SPDX-License-Identifier: AGPL-3.0-only
"""Daily goal, streak and study statistics (F-PROG-1, F-PROG-3).

Everything is derived from review logs and schedules at read time, in the user's
own time zone, so changing the goal or the zone re-reads history consistently
and there is no counter to drift out of step with the logs.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from flashcards.application.deck_service import DeckService
from flashcards.domain.ports import Clock, StudySettingsRepository, StudyStatsRepository
from flashcards.domain.progress import (
    DEFAULT_FORECAST_DAYS,
    DEFAULT_HEATMAP_DAYS,
    MAX_FORECAST_DAYS,
    MAX_HEATMAP_DAYS,
    STREAK_LOOKBACK_DAYS,
    DailyProgress,
    ProgressStats,
    StudySettings,
    check_range,
    current_streak,
    daily_counts,
    day_start,
    forecast,
    heatmap,
    local_date,
    missed_days,
    retention_curve,
    study_settings,
)


class ProgressService:
    def __init__(
        self,
        decks: DeckService,
        settings: StudySettingsRepository,
        stats: StudyStatsRepository,
        clock: Clock,
    ) -> None:
        self._decks = decks
        self._settings = settings
        self._stats = stats
        self._clock = clock

    async def get_settings(self, *, owner_id: str) -> StudySettings:
        return await self._settings.get(owner_id) or StudySettings(owner_id=owner_id)

    async def update_settings(
        self, *, owner_id: str, daily_goal: int | None = None, time_zone: str | None = None
    ) -> StudySettings:
        current = await self.get_settings(owner_id=owner_id)
        updated = study_settings(
            owner_id,
            daily_goal=current.daily_goal if daily_goal is None else daily_goal,
            time_zone=current.time_zone if time_zone is None else time_zone,
        )
        await self._settings.save(updated)
        return updated

    async def today(self, *, owner_id: str, with_streak: bool = True) -> DailyProgress:
        """Today's reviews against the daily goal; the streak only when asked
        (it reads up to a year of history, so the answer path skips it)."""
        settings = await self.get_settings(owner_id=owner_id)
        zone = settings.zone
        now = self._clock.now()
        today = local_date(now, zone)
        first = today - timedelta(days=STREAK_LOOKBACK_DAYS) if with_streak else today
        buckets = await self._stats.review_activity(
            owner_id,
            since=day_start(first, zone),
            until=day_start(today + timedelta(days=1), zone),
        )
        per_day = {day: c.reviews for day, c in daily_counts(buckets, zone).items()}
        return DailyProgress(
            day=today,
            time_zone=settings.time_zone,
            reviews=per_day.get(today, 0),
            daily_goal=settings.daily_goal,
            streak=current_streak(per_day, today, settings.daily_goal) if with_streak else None,
        )

    async def stats(
        self,
        *,
        owner_id: str,
        deck_id: str | None = None,
        days: int = DEFAULT_HEATMAP_DAYS,
        forecast_days: int = DEFAULT_FORECAST_DAYS,
    ) -> ProgressStats:
        """Heatmap and retention over the last `days` local days (today included)
        and the review forecast for the next `forecast_days`, overall or per deck."""
        check_range(days, MAX_HEATMAP_DAYS, "days")
        check_range(forecast_days, MAX_FORECAST_DAYS, "forecast_days")
        deck_ids: tuple[str, ...] | None = None
        if deck_id is not None:
            await self._decks.get(owner_id=owner_id, deck_id=deck_id)  # raises DeckNotFound
            deck_ids = (deck_id,)
        settings = await self.get_settings(owner_id=owner_id)
        zone = settings.zone
        today = local_date(self._clock.now(), zone)
        first = today - timedelta(days=days - 1)
        today_start = day_start(today, zone)
        activity = await self._stats.review_activity(
            owner_id,
            since=day_start(first, zone),
            until=day_start(today + timedelta(days=1), zone),
            deck_ids=deck_ids,
        )
        counts = daily_counts(activity, zone)
        due = await self._stats.due_forecast(
            owner_id,
            floor=today_start,
            until=day_start(today + timedelta(days=forecast_days), zone),
            deck_ids=deck_ids,
        )
        return ProgressStats(
            time_zone=settings.time_zone,
            deck_id=deck_id,
            heatmap=heatmap(counts, first, today),
            retention=retention_curve(counts.values()),
            forecast=forecast(due, zone, today, forecast_days),
        )

    async def missed_days_since(
        self, *, owner_id: str, deck_ids: Sequence[str], since: datetime
    ) -> int:
        """Local days strictly after `since`'s day and before today with no
        reviews in these decks (bounded to STREAK_LOOKBACK_DAYS)."""
        settings = await self.get_settings(owner_id=owner_id)
        zone = settings.zone
        now = self._clock.now()
        today = local_date(now, zone)
        after = local_date(since, zone)
        first: date = max(after, today - timedelta(days=STREAK_LOOKBACK_DAYS))
        buckets = await self._stats.review_activity(
            owner_id,
            since=day_start(first, zone),
            until=day_start(today, zone),
            deck_ids=tuple(deck_ids),
        )
        per_day = {day: c.reviews for day, c in daily_counts(buckets, zone).items()}
        return missed_days(per_day, after, today)
