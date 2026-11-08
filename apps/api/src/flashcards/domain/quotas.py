# SPDX-License-Identifier: AGPL-3.0-only
"""Per-user AI quotas (F-AI-11, F-ADMIN-3).

Two budgets share one counter store: monthly document pages for card generation,
and a daily count of synchronous AI calls (grading, summaries, mock exams, chat),
kept apart by their period keys.
"""

from __future__ import annotations

from datetime import datetime

DEFAULT_MONTHLY_PAGES = 10
# Synchronous AI calls per user per day (each one is a model request).
DEFAULT_DAILY_AI_CALLS = 200


class QuotaExceeded(Exception):
    """The owner has no remaining page quota for the period."""


def billing_period(now: datetime) -> str:
    """The monthly period key a quota is counted against, e.g. '2026-11'."""
    return now.strftime("%Y-%m")


class AiCallLimitReached(Exception):
    """The owner has used today's synchronous AI call budget."""


def ai_call_period(now: datetime) -> str:
    """The daily period key for synchronous AI calls, e.g. 'calls:2026-11-08'.

    The prefix keeps it from ever colliding with a monthly page period.
    """
    return now.strftime("calls:%Y-%m-%d")
