# SPDX-License-Identifier: AGPL-3.0-only
"""Per-user AI page quota (F-AI-11, F-ADMIN-3)."""

from __future__ import annotations

from datetime import datetime

DEFAULT_MONTHLY_PAGES = 10


class QuotaExceeded(Exception):
    """The owner has no remaining page quota for the period."""


def billing_period(now: datetime) -> str:
    """The monthly period key a quota is counted against, e.g. '2026-11'."""
    return now.strftime("%Y-%m")
