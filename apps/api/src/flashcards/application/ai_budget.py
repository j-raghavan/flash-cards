# SPDX-License-Identifier: AGPL-3.0-only
"""The budget for synchronous AI calls (F-AI-11, F-ADMIN-4).

Grading, summaries, mock exams and document chat each call the model inside the
request. Every such call is charged here first, just before the model request:
the instance's monthly spend cap is checked, then the user's daily call
allowance, so an account cannot run up unbounded provider spend. After the call,
its tokens and cost are recorded. In SQL mode both share the request's
transaction, so a call that fails is neither charged nor recorded.
"""

from __future__ import annotations

from flashcards.application.ai_spend import AiSpend
from flashcards.domain.ai import StructuredResult
from flashcards.domain.ports import Clock, QuotaRepository
from flashcards.domain.quotas import AiCallLimitReached, ai_call_period
from flashcards.domain.usage import AiFeature


class AiCallBudget:
    def __init__(self, calls: QuotaRepository, clock: Clock, spend: AiSpend) -> None:
        # The repository's own limit is the daily allowance.
        self._calls = calls
        self._clock = clock
        self._spend = spend

    async def charge(self, owner_id: str) -> None:
        """Raise SpendCapReached or AiCallLimitReached, else count the call."""
        await self._spend.ensure_available()
        period = ai_call_period(self._clock.now())
        if not await self._calls.try_reserve_pages(owner_id, 1, period):
            raise AiCallLimitReached(owner_id)

    async def record(self, owner_id: str, feature: AiFeature, result: StructuredResult) -> None:
        """Record a finished call's tokens and cost."""
        await self._spend.record(owner_id, feature, result)
