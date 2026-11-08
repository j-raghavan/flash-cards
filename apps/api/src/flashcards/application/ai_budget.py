# SPDX-License-Identifier: AGPL-3.0-only
"""The daily budget for synchronous AI calls (F-AI-11).

Grading, summaries, mock exams and document chat each call the model inside the
request. Every such call is charged here first, just before the model request, so
an account cannot run up unbounded provider spend. In SQL mode the charge shares
the request's transaction, so a call that fails is not charged.
"""

from __future__ import annotations

from flashcards.domain.ports import Clock, QuotaRepository
from flashcards.domain.quotas import AiCallLimitReached, ai_call_period


class AiCallBudget:
    def __init__(self, calls: QuotaRepository, clock: Clock) -> None:
        # The repository's own limit is the daily allowance.
        self._calls = calls
        self._clock = clock

    async def charge(self, owner_id: str) -> None:
        period = ai_call_period(self._clock.now())
        if not await self._calls.try_reserve_pages(owner_id, 1, period):
            raise AiCallLimitReached(owner_id)
