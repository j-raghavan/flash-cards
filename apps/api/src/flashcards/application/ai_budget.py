# SPDX-License-Identifier: AGPL-3.0-only
"""The budget for synchronous AI calls (F-AI-11, F-ADMIN-4).

Grading, summaries, mock exams and document chat each call the model inside the
request. Every such call is charged here first, just before the model request:
the instance's monthly spend cap is checked, then the user's daily call
allowance, so an account cannot run up unbounded provider spend.

Once the model answers, its tokens and cost are recorded and committed together
with the charge, before the answer is parsed. The provider has billed the call
by then, so output that later fails validation (and rolls the rest of the
request back) still counts against the daily allowance and the spend cap.
A call that fails at the provider is neither charged nor recorded.
"""

from __future__ import annotations

from flashcards.application.ai_spend import AiSpend
from flashcards.domain.ai import StructuredResult
from flashcards.domain.ports import Clock, QuotaRepository, UnitOfWork
from flashcards.domain.quotas import AiCallLimitReached, ai_call_period
from flashcards.domain.usage import AiFeature


class AiCallBudget:
    def __init__(
        self, calls: QuotaRepository, clock: Clock, spend: AiSpend, unit_of_work: UnitOfWork
    ) -> None:
        # The repository's own limit is the daily allowance.
        self._calls = calls
        self._clock = clock
        self._spend = spend
        self._uow = unit_of_work

    async def charge(self, owner_id: str) -> None:
        """Raise SpendCapReached or AiCallLimitReached, else count the call."""
        await self._spend.ensure_available()
        period = ai_call_period(self._clock.now())
        if not await self._calls.try_reserve_pages(owner_id, 1, period):
            raise AiCallLimitReached(owner_id)

    async def record(self, owner_id: str, feature: AiFeature, result: StructuredResult) -> None:
        """Record a finished call's tokens and cost, and commit them with the charge."""
        await self._spend.record(owner_id, feature, result)
        await self._uow.commit()
