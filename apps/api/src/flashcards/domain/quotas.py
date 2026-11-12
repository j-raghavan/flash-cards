# SPDX-License-Identifier: AGPL-3.0-only
"""Per-user AI quotas (F-AI-11, F-ADMIN-3).

Two budgets share one counter store: monthly document pages for card generation,
and a daily count of synchronous AI calls (grading, summaries, mock exams, chat),
kept apart by their period keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# The historical free-tier allowance, and the page repositories' constructor
# default. The instance default (InstanceQuotas.default_monthly_pages) is unlimited
# out of the box (None), and the page-reservation path always passes an explicit
# limit, so this value is not consulted for page quotas; an operator reinstates a
# free tier by setting FLASHCARDS_AI_DEFAULT_MONTHLY_PAGES (ADR 0027).
DEFAULT_MONTHLY_PAGES = 10
# Upper bound an admin may set for a finite monthly page limit (a sanity bound, not
# a cost). None always means unlimited and is not bounded by this.
MAX_MONTHLY_PAGES = 100_000
MICRO_USD_PER_USD = 1_000_000
# Synchronous AI calls per user per day (each one is a model request).
DEFAULT_DAILY_AI_CALLS = 200
# Cards a user may create by cloning shared decks per day.
DEFAULT_DAILY_CLONE_CARDS = 50_000


# The reservation limit used for an admin's pages (ADR 0022): they are counted, so
# the usage report shows them, but never refused. Far below the INTEGER column's
# maximum (2,147,483,647), so `used_pages + pages` in the reservation's guard
# cannot overflow while it is still compared against this limit.
UNMETERED_PAGE_LIMIT = 1_000_000_000


class QuotaExceeded(Exception):
    """The owner has no remaining page quota for the period.

    `pages` is what the refused document needed, `remaining` what the owner had
    left and `limit` their monthly limit, when known. The message is shown to the
    user as is, so it says them.
    """

    def __init__(
        self,
        *,
        pages: int | None = None,
        remaining: int | None = None,
        limit: int | None = None,
    ) -> None:
        super().__init__(_quota_message(pages, remaining, limit))
        self.pages = pages
        self.remaining = remaining
        self.limit = limit


def _pages(n: int) -> str:
    return f"{n} page" if n == 1 else f"{n} pages"


def _quota_message(pages: int | None, remaining: int | None, limit: int | None) -> str:
    document = "" if pages is None else f" This document has {_pages(pages)}."
    if limit == 0:
        return f"AI generation is not included in your account this month.{document}"
    if remaining is None or (pages is None and remaining > 0):
        return "AI page quota exceeded for this period."
    if remaining <= 0 or pages is None:
        return (
            "You have no AI pages left this month; the allowance renews at the start "
            f"of next month.{document}"
        )
    return (
        f"This document has {_pages(pages)}, but you have only "
        f"{_pages(remaining)} of AI generation left this month."
    )


class InvalidQuota(ValueError):
    """A quota setting outside the allowed range."""


@dataclass(frozen=True, slots=True)
class InstanceQuotas:
    """Instance-wide AI limits an admin sets (F-ADMIN-3, F-AI-11).

    default_monthly_pages applies to every user without an override; None means
    members have no monthly page limit (like admins, ADR 0027): their pages are
    still counted for the usage report but generation is never refused by the
    quota, leaving the spend cap as the only brake.
    monthly_spend_cap_micro_usd stops new AI work for everyone once the month's
    recorded spend reaches it; None means no cap.
    """

    default_monthly_pages: int | None = None
    monthly_spend_cap_micro_usd: int | None = None

    def __post_init__(self) -> None:
        check_monthly_pages(self.default_monthly_pages)
        if self.monthly_spend_cap_micro_usd is not None and self.monthly_spend_cap_micro_usd < 0:
            raise InvalidQuota("the spend cap cannot be negative")


def check_monthly_pages(pages: int | None) -> None:
    """Validate a finite monthly page limit; None (unlimited) is always allowed."""
    if pages is not None and not 0 <= pages <= MAX_MONTHLY_PAGES:
        raise InvalidQuota(f"monthly pages must be between 0 and {MAX_MONTHLY_PAGES}")


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


def clone_period(now: datetime) -> str:
    """The daily period key for cloned cards, e.g. 'clones:2026-11-08'."""
    return now.strftime("clones:%Y-%m-%d")
