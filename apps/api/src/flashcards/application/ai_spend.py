# SPDX-License-Identifier: AGPL-3.0-only
"""The instance's monthly AI spend: recording it and capping it (F-AI-11, F-ADMIN-4).

Every model call is recorded with the tokens the provider reported and its cost
at the operator's configured prices.

The cap is enforced with holds (ADR 0019). Before each model call, an estimate
of its cost is held against the cap, atomically and visibly to every other
request: the hold succeeds only while the month's recorded cost plus the holds
of calls still running is under the cap. Recording the call's usage settles the
hold in the same transaction; a call that fails releases it. Concurrent calls
therefore cannot jointly pass the cap by more than one call's estimate, plus
whatever the running calls cost beyond their estimates (only possible for image
input, whose token count is estimated rather than bounded).

New work (a generation job or its retry) is also refused up front once the cap
is reached, so it never queues; each page the job generates still takes its
own hold.
"""

from __future__ import annotations

import logging
import uuid

from flashcards.domain.ai import StructuredRequest, StructuredResult
from flashcards.domain.ports import (
    Clock,
    QuotaSettingsRepository,
    SpendHoldRepository,
    UsageRepository,
)
from flashcards.domain.quotas import InstanceQuotas, billing_period
from flashcards.domain.usage import (
    HOLD_TTL,
    AiFeature,
    AiUsage,
    SpendCapReached,
    SpendHold,
    TokenPrices,
)

logger = logging.getLogger("flashcards")


class AiSpend:
    def __init__(
        self,
        usage: UsageRepository,
        settings: QuotaSettingsRepository,
        clock: Clock,
        holds: SpendHoldRepository,
        *,
        provider: str,
        prices: TokenPrices,
        defaults: InstanceQuotas,
    ) -> None:
        self._usage = usage
        self._settings = settings
        self._holds = holds
        self._defaults = defaults
        self._clock = clock
        self._provider = provider
        self._prices = prices

    async def _cap(self) -> int | None:
        quotas = await self._settings.get_instance() or self._defaults
        return quotas.monthly_spend_cap_micro_usd

    async def ensure_available(self) -> None:
        """Raise SpendCapReached when this month's spend and holds have reached the cap."""
        cap = await self._cap()
        if cap is None:
            return
        now = self._clock.now()
        period = billing_period(now)
        if await self._usage.total_cost(period) + await self._holds.held(period, now) >= cap:
            raise SpendCapReached()

    async def hold(self, request: StructuredRequest) -> SpendHold | None:
        """Hold the estimated cost of `request` against the cap, or raise SpendCapReached.

        None when no cap is set: there is nothing to hold against.
        """
        cap = await self._cap()
        if cap is None:
            return None
        now = self._clock.now()
        hold = SpendHold(
            id=str(uuid.uuid4()),
            period=billing_period(now),
            amount_micro_usd=self._prices.estimate_micro_usd(request),
            expires_at=now + HOLD_TTL,
        )
        if not await self._holds.try_hold(hold, cap_micro_usd=cap, now=now):
            raise SpendCapReached()
        return hold

    async def release(self, hold: SpendHold | None) -> None:
        """Give a hold back after its call failed. Best effort: a hold left behind lapses."""
        if hold is None:
            return
        try:
            await self._holds.release(hold.id)
        except Exception:
            logger.warning("could not release AI spend hold %s; it lapses", hold.id, exc_info=True)

    async def record(
        self,
        owner_id: str,
        feature: AiFeature,
        result: StructuredResult,
        hold: SpendHold | None = None,
    ) -> AiUsage:
        """Record one model call's tokens and cost against the owner, settling its hold."""
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
        if hold is not None:
            await self._holds.settle(hold.id)
        return usage
