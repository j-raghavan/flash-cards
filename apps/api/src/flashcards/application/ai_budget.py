# SPDX-License-Identifier: AGPL-3.0-only
"""The budget for synchronous AI calls (F-AI-11, F-ADMIN-4).

Grading, summaries, mock exams and document chat each call the model inside the
request. Every such call is charged here first, around the model request:
the call's estimated cost is held against the instance's monthly spend cap
(ADR 0019), then the user's daily call allowance is counted, so an account
cannot run up unbounded provider spend:

    async with budget.charge(owner_id, request) as hold:
        result = await llm.generate_structured(request)
    await budget.record(owner_id, feature, result, hold)

Once the model answers, its tokens and cost are recorded and committed together
with the charge, settling the hold, before the answer is parsed. The provider
has billed the call by then, so output that later fails validation (and rolls
the rest of the request back) still counts against the daily allowance and the
spend cap. A call that fails at the provider is neither charged nor recorded,
and its hold is released.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from flashcards.application.ai_spend import AiSpend
from flashcards.domain.ai import StructuredRequest, StructuredResult
from flashcards.domain.ports import Clock, QuotaRepository, UnitOfWork
from flashcards.domain.quotas import AiCallLimitReached, ai_call_period
from flashcards.domain.usage import AiFeature, SpendHold


class AiCallBudget:
    def __init__(
        self, calls: QuotaRepository, clock: Clock, spend: AiSpend, unit_of_work: UnitOfWork
    ) -> None:
        # The repository's own limit is the daily allowance.
        self._calls = calls
        self._clock = clock
        self._spend = spend
        self._uow = unit_of_work

    @asynccontextmanager
    async def charge(
        self, owner_id: str, request: StructuredRequest
    ) -> AsyncIterator[SpendHold | None]:
        """Hold the call's cost and count it, for the model call made inside the block.

        Raises SpendCapReached or AiCallLimitReached before the block runs. If the
        block raises, the hold is released and the error propagates.
        """
        hold = await self._spend.hold(request)
        try:
            period = ai_call_period(self._clock.now())
            if not await self._calls.try_reserve_pages(owner_id, 1, period):
                raise AiCallLimitReached(owner_id)
            yield hold
        except BaseException:
            await self._spend.release(hold)
            raise

    async def record(
        self,
        owner_id: str,
        feature: AiFeature,
        result: StructuredResult,
        hold: SpendHold | None,
    ) -> None:
        """Record a finished call's tokens and cost, and commit them with the charge."""
        await self._spend.record(owner_id, feature, result, hold)
        await self._uow.commit()
