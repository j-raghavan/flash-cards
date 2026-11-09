# SPDX-License-Identifier: AGPL-3.0-only
"""Day boundaries, streaks, heatmap, retention and forecast (F-PROG-1, F-PROG-3)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from flashcards.domain.progress import (
    MAX_DAILY_GOAL,
    STREAK_LOOKBACK_DAYS,
    ActivityBucket,
    DailyProgress,
    DayCount,
    DueBucket,
    InvalidRange,
    InvalidStudySettings,
    RetentionPoint,
    StudySettings,
    bucket_start,
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
    validate_daily_goal,
    validate_time_zone,
)

BERLIN = ZoneInfo("Europe/Berlin")
KOLKATA = ZoneInfo("Asia/Kolkata")
UTC_ZONE = ZoneInfo("UTC")


def test_daily_goal_bounds() -> None:
    assert validate_daily_goal(1) == 1
    assert validate_daily_goal(MAX_DAILY_GOAL) == MAX_DAILY_GOAL
    for bad in (0, -5, MAX_DAILY_GOAL + 1):
        with pytest.raises(InvalidStudySettings):
            validate_daily_goal(bad)


def test_time_zone_must_be_a_known_iana_name() -> None:
    assert validate_time_zone(" Europe/Berlin ") == "Europe/Berlin"
    assert validate_time_zone("UTC") == "UTC"
    for bad in ("", "Mars/Olympus", "europe/berlin", "x" * 65, "../etc/passwd"):
        with pytest.raises(InvalidStudySettings):
            validate_time_zone(bad)


def test_settings_defaults_and_factory() -> None:
    default = StudySettings(owner_id="a")
    assert (default.daily_goal, default.time_zone) == (20, "UTC")
    assert default.zone == UTC_ZONE
    built = study_settings("a", daily_goal=5, time_zone="Asia/Kolkata")
    assert built == StudySettings("a", 5, "Asia/Kolkata")


def test_bucket_start_is_epoch_aligned_quarter_hour() -> None:
    moment = datetime(2026, 11, 8, 12, 29, 59, 999, tzinfo=UTC)
    assert bucket_start(moment) == datetime(2026, 11, 8, 12, 15, tzinfo=UTC)
    # A non-UTC aware time lands in the same absolute bucket.
    assert bucket_start(moment.astimezone(KOLKATA)) == datetime(2026, 11, 8, 12, 15, tzinfo=UTC)


def test_day_start_follows_daylight_saving() -> None:
    # Berlin: UTC+2 in summer, UTC+1 after the last Sunday of October.
    assert day_start(date(2026, 7, 1), BERLIN) == datetime(2026, 6, 30, 22, 0, tzinfo=UTC)
    assert day_start(date(2026, 11, 8), BERLIN) == datetime(2026, 11, 7, 23, 0, tzinfo=UTC)
    # Kolkata is UTC+5:30, a half-hour offset that still aligns with the buckets.
    assert day_start(date(2026, 11, 8), KOLKATA) == datetime(2026, 11, 7, 18, 30, tzinfo=UTC)
    assert local_date(datetime(2026, 11, 7, 18, 30, tzinfo=UTC), KOLKATA) == date(2026, 11, 8)
    assert local_date(datetime(2026, 11, 7, 18, 29, tzinfo=UTC), KOLKATA) == date(2026, 11, 7)


def test_daily_counts_use_the_users_day_boundary() -> None:
    late = datetime(2026, 11, 7, 23, 30, tzinfo=UTC)  # 00:30 on the 8th in Berlin
    early = datetime(2026, 11, 8, 8, 0, tzinfo=UTC)
    buckets = [ActivityBucket(late, 3, 2), ActivityBucket(early, 2, 2)]
    assert daily_counts(buckets, UTC_ZONE) == {
        date(2026, 11, 7): DayCount(date(2026, 11, 7), 3, 2),
        date(2026, 11, 8): DayCount(date(2026, 11, 8), 2, 2),
    }
    assert daily_counts(buckets, BERLIN) == {date(2026, 11, 8): DayCount(date(2026, 11, 8), 5, 4)}


def test_streak_counts_back_from_today_or_yesterday() -> None:
    today = date(2026, 11, 8)
    met = {today - timedelta(days=n): 20 for n in range(1, 4)}  # three days before today
    # Today not met yet: the streak still stands on yesterday.
    assert current_streak({**met, today: 5}, today, 20) == 3
    # Today met: it extends the streak.
    assert current_streak({**met, today: 20}, today, 20) == 4
    # A gap breaks it.
    gap = {**met, today - timedelta(days=2): 19}
    assert current_streak(gap, today, 20) == 1
    assert current_streak({}, today, 20) == 0


def test_streak_is_capped_at_the_lookback() -> None:
    today = date(2026, 11, 8)
    every_day = {today - timedelta(days=n): 1 for n in range(STREAK_LOOKBACK_DAYS + 30)}
    assert current_streak(every_day, today, 1) == STREAK_LOOKBACK_DAYS


def test_daily_progress_completion_signal() -> None:
    def progress(reviews: int) -> DailyProgress:
        return DailyProgress(date(2026, 11, 8), "UTC", reviews, 10)

    assert not progress(9).goal_met and not progress(9).just_completed
    assert progress(10).goal_met and progress(10).just_completed
    assert progress(11).goal_met and not progress(11).just_completed
    assert progress(10).streak is None


def test_heatmap_is_zero_filled_per_day() -> None:
    first, last = date(2026, 11, 1), date(2026, 11, 3)
    counts = {date(2026, 11, 2): DayCount(date(2026, 11, 2), 7, 6)}
    assert [(d.day.day, d.reviews) for d in heatmap(counts, first, last)] == [
        (1, 0),
        (2, 7),
        (3, 0),
    ]


def test_retention_curve_groups_by_monday_week() -> None:
    counts = [
        DayCount(date(2026, 11, 2), 10, 9),  # Monday
        DayCount(date(2026, 11, 8), 10, 7),  # Sunday, same week
        DayCount(date(2026, 11, 9), 4, 4),  # next Monday
    ]
    curve = retention_curve(reversed(counts))
    assert curve == (
        RetentionPoint(date(2026, 11, 2), 20, 16),
        RetentionPoint(date(2026, 11, 9), 4, 4),
    )
    assert curve[0].rate == 0.8
    assert RetentionPoint(date(2026, 11, 2), 0, 0).rate is None


def test_forecast_counts_overdue_today_and_zero_fills() -> None:
    today = date(2026, 11, 8)
    buckets = [
        DueBucket(datetime(2026, 11, 1, 9, 0, tzinfo=UTC), 4),  # overdue, not clamped
        DueBucket(datetime(2026, 11, 8, 0, 0, tzinfo=UTC), 1),
        DueBucket(datetime(2026, 11, 9, 23, 45, tzinfo=UTC), 2),  # 00:45 on the 10th in Berlin
    ]
    assert [d.due for d in forecast(buckets, UTC_ZONE, today, 3)] == [5, 2, 0]
    assert [d.due for d in forecast(buckets, BERLIN, today, 3)] == [5, 0, 2]
    assert [d.day for d in forecast([], UTC_ZONE, today, 2)] == [today, date(2026, 11, 9)]


def test_missed_days_are_strictly_between_and_capped() -> None:
    after, before = date(2026, 11, 1), date(2026, 11, 8)
    reviews = {date(2026, 11, 3): 4, date(2026, 11, 5): 1}
    # The 2nd..7th is six days; two had reviews.
    assert missed_days(reviews, after, before) == 4
    assert missed_days({}, before, before) == 0
    assert missed_days({}, before - timedelta(days=1), before) == 0
    assert missed_days({}, date(2020, 1, 1), before) == STREAK_LOOKBACK_DAYS


def test_check_range() -> None:
    assert check_range(5, 10, "days") == 5
    for bad in (0, 11):
        with pytest.raises(InvalidRange):
            check_range(bad, 10, "days")
