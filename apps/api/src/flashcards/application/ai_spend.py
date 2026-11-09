# SPDX-License-Identifier: AGPL-3.0-only
"""The instance's monthly AI spend: recording it and capping it (F-AI-11, F-ADMIN-4).

Every model call is recorded with the tokens the provider reported and its cost
at the operator's configured prices. Before new AI work starts (a synchronous
call, a generation job or its retry), the month's recorded cost is checked
against the cap, and the work is refused once the cap is reached.

The cost of a call is known only after it returns, so the check cannot reserve
spend: calls already running when the cap is reached still finish, and the cap
can be overshot by the work in flight at that moment.
"""

from __future__ import annotations

import uuid

from flashcards.domain.ai import StructuredResult
from flashcards.domain.ports import Clock, QuotaSettingsRepository, UsageRepository
from flashcards.domain.quotas import InstanceQuotas, billing_period
from flashcards.domain.usage import AiFeature, AiUsage, SpendCapReached, TokenPrices


class AiSpend:
    def __init__(
        self,
        usage: UsageRepository,
        settings: QuotaSettingsRepository,
        clock: Clock,
        *,
        provider: str,
        prices: TokenPrices,
        defaults: InstanceQuotas,
    ) -> None:
        self._usage = usage
        self._settings = settings
        self._defaults = defaults
        self._clock = clock
        self._provider = provider
        self._prices = prices

    async def ensure_available(self) -> None:
        """Raise SpendCapReached when this month's spend has reached the cap."""
        quotas = await self._settings.get_instance() or self._defaults
        cap = quotas.monthly_spend_cap_micro_usd
        if cap is None:
            return
        if await self._usage.total_cost(billing_period(self._clock.now())) >= cap:
            raise SpendCapReached()

    async def record(self, owner_id: str, feature: AiFeature, result: StructuredResult) -> AiUsage:
        """Record one model call's tokens and cost against the owner."""
        now = self._clock.now()
        usage = AiUsage(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            provider=self._provider,
            model=result.model,
            feature=feature,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cost_micro_usd=self._prices.cost_micro_usd(result.usage),
            period=billing_period(now),
            created_at=now,
        )
        await self._usage.add(usage)
        return usage
