# SPDX-License-Identifier: AGPL-3.0-only
"""Daily goal, streak, heatmap, retention and forecast (F-PROG-1, F-PROG-3).

The server does not know where the user is, so the day boundary is the user's own
local midnight in an IANA time zone they choose (UTC by default). Storage only
ever aggregates review and due times into epoch-aligned 15-minute buckets; this
module turns those buckets into local days. Every time zone in use today has an
offset that is a whole number of quarter hours, so a bucket never straddles a
local midnight, and the database never has to know the zone rules: memory and
SQL adapters agree by construction.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from zoneinfo import ZoneInfo, available_timezones

BUCKET_SECONDS = 15 * 60

DEFAULT_DAILY_GOAL = 20
MAX_DAILY_GOAL = 1000
DEFAULT_TIME_ZONE = "UTC"
MAX_TIME_ZONE_LENGTH = 64

# Query bounds: every range read from storage is capped so one request does a
# bounded amount of work however long the history is.
MAX_HEATMAP_DAYS = 366
DEFAULT_HEATMAP_DAYS = 90
MAX_FORECAST_DAYS = 90
DEFAULT_FORECAST_DAYS = 30
# A streak is counted back at most this many days (a longer streak reads as this).
STREAK_LOOKBACK_DAYS = 366


class ProgressError(Exception):
    """Base class for progress failures."""


class InvalidStudySettings(ProgressError):
    pass


class InvalidRange(ProgressError):
    pass


def validate_daily_goal(daily_goal: int) -> int:
    if not 1 <= daily_goal <= MAX_DAILY_GOAL:
        raise InvalidStudySettings(f"daily_goal must be between 1 and {MAX_DAILY_GOAL}")
    return daily_goal


def validate_time_zone(name: str) -> str:
    """Accept only a known IANA zone name (zoneinfo), e.g. "Europe/Berlin"."""
    cleaned = name.strip()
    if len(cleaned) > MAX_TIME_ZONE_LENGTH or cleaned not in _known_zones():
        raise InvalidStudySettings(f"unknown time zone: {name!r}")
    return cleaned


@cache
def _known_zones() -> frozenset[str]:
    return frozenset(available_timezones())


@dataclass(frozen=True, slots=True)
class StudySettings:
    owner_id: str
    daily_goal: int = DEFAULT_DAILY_GOAL
    time_zone: str = DEFAULT_TIME_ZONE

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.time_zone)


def study_settings(owner_id: str, *, daily_goal: int, time_zone: str) -> StudySettings:
    return StudySettings(
        owner_id=owner_id,
        daily_goal=validate_daily_goal(daily_goal),
        time_zone=validate_time_zone(time_zone),
    )


@dataclass(frozen=True, slots=True)
class ActivityBucket:
    """Reviews whose time falls in [start, start + 15 minutes); start is UTC."""

    start: datetime
    reviews: int
    recalled: int  # rated Hard, Good or Easy (not Again)


@dataclass(frozen=True, slots=True)
class DueBucket:
    """Scheduled cards due in [start, start + 15 minutes); start is UTC."""

    start: datetime
    cards: int


def bucket_start(moment: datetime) -> datetime:
    """The UTC start of the epoch-aligned 15-minute bucket holding a moment."""
    seconds = int(moment.timestamp())
    return datetime.fromtimestamp(seconds - seconds % BUCKET_SECONDS, UTC)


def local_date(moment: datetime, zone: ZoneInfo) -> date:
    return moment.astimezone(zone).date()


def day_start(day: date, zone: ZoneInfo) -> datetime:
    """The UTC instant of local midnight starting a day (DST-aware)."""
    return datetime.combine(day, time(0), tzinfo=zone).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class DayCount:
    day: date
    reviews: int
    recalled: int


def daily_counts(buckets: Iterable[ActivityBucket], zone: ZoneInfo) -> dict[date, DayCount]:
    """Sum activity buckets into local days."""
    totals: dict[date, tuple[int, int]] = {}
    for bucket in buckets:
        day = local_date(bucket.start, zone)
        reviews, recalled = totals.get(day, (0, 0))
        totals[day] = (reviews + bucket.reviews, recalled + bucket.recalled)
    return {day: DayCount(day, r, k) for day, (r, k) in totals.items()}


def current_streak(reviews_by_day: dict[date, int], today: date, goal: int) -> int:
    """Consecutive days meeting the goal, ending today or, if today is not met yet,
    yesterday: an unfinished today never breaks a streak."""
    day = today if reviews_by_day.get(today, 0) >= goal else today - timedelta(days=1)
    streak = 0
    while streak < STREAK_LOOKBACK_DAYS and reviews_by_day.get(day, 0) >= goal:
        streak += 1
        day -= timedelta(days=1)
    return streak


@dataclass(frozen=True, slots=True)
class DailyProgress:
    day: date
    time_zone: str
    reviews: int
    daily_goal: int
    streak: int | None = None  # None when not computed

    @property
    def goal_met(self) -> bool:
        return self.reviews >= self.daily_goal

    @property
    def just_completed(self) -> bool:
        """True exactly when the latest review is the one that reached the goal."""
        return self.reviews == self.daily_goal


@dataclass(frozen=True, slots=True)
class HeatmapDay:
    day: date
    reviews: int


@dataclass(frozen=True, slots=True)
class RetentionPoint:
    week_start: date  # the Monday starting the local week
    reviews: int
    recalled: int

    @property
    def rate(self) -> float | None:
        return round(self.recalled / self.reviews, 4) if self.reviews else None


@dataclass(frozen=True, slots=True)
class ForecastDay:
    day: date
    due: int


@dataclass(frozen=True, slots=True)
class ProgressStats:
    time_zone: str
    deck_id: str | None
    heatmap: tuple[HeatmapDay, ...]
    retention: tuple[RetentionPoint, ...]
    forecast: tuple[ForecastDay, ...]


def check_range(days: int, maximum: int, name: str) -> int:
    if not 1 <= days <= maximum:
        raise InvalidRange(f"{name} must be between 1 and {maximum}")
    return days


def heatmap(counts: dict[date, DayCount], first: date, last: date) -> tuple[HeatmapDay, ...]:
    """One entry per local day from first to last inclusive, zero-filled."""
    span = (last - first).days + 1
    days = (first + timedelta(days=offset) for offset in range(span))
    return tuple(HeatmapDay(day, counts[day].reviews if day in counts else 0) for day in days)


def retention_curve(counts: Iterable[DayCount]) -> tuple[RetentionPoint, ...]:
    """Actual recall rate per local week (Monday start), oldest first; weeks
    without reviews are omitted, since they carry no evidence either way."""
    weeks: dict[date, tuple[int, int]] = {}
    for count in counts:
        week = count.day - timedelta(days=count.day.weekday())
        reviews, recalled = weeks.get(week, (0, 0))
        weeks[week] = (reviews + count.reviews, recalled + count.recalled)
    return tuple(RetentionPoint(week, r, k) for week, (r, k) in sorted(weeks.items()))


def forecast(
    buckets: Iterable[DueBucket], zone: ZoneInfo, today: date, days: int
) -> tuple[ForecastDay, ...]:
    """Cards coming due per local day from today on; overdue cards count today
    (the adapter clamps them to the start of today)."""
    per_day: dict[date, int] = {}
    for bucket in buckets:
        day = max(local_date(bucket.start, zone), today)
        per_day[day] = per_day.get(day, 0) + bucket.cards
    return tuple(
        ForecastDay(today + timedelta(days=n), per_day.get(today + timedelta(days=n), 0))
        for n in range(days)
    )


def missed_days(reviews_by_day: dict[date, int], after: date, before: date) -> int:
    """Days strictly between two dates with no reviews at all (capped)."""
    first = max(after + timedelta(days=1), before - timedelta(days=STREAK_LOOKBACK_DAYS))
    span = max(0, (before - first).days)
    return sum(
        1 for n in range(span) if reviews_by_day.get(first + timedelta(days=n), 0) == 0
    )

