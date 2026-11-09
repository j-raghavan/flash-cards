# SPDX-License-Identifier: AGPL-3.0-only
"""AI usage and cost records (F-ADMIN-4, F-AI-11).

Every model call is recorded with the tokens the provider reported and a cost
computed from the operator's configured prices. Records carry user ids, never
content, so they can be kept for cost accounting after an account is deleted.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

from flashcards.domain.ai import TokenUsage
from flashcards.domain.quotas import InstanceQuotas

TOKENS_PER_MILLION = 1_000_000


class AiFeature(enum.StrEnum):
    GENERATION = "generation"
    GRADING = "grading"
    SUMMARY = "summary"
    MOCK_EXAM = "mock_exam"
    CHAT = "chat"


class SpendCapReached(Exception):
    """The instance's monthly AI spend cap is used up; no new AI work starts."""


@dataclass(frozen=True, slots=True)
class TokenPrices:
    """What the operator pays the provider, in US dollars per million tokens."""

    input_usd_per_mtok: float = 0.0
    output_usd_per_mtok: float = 0.0

    def cost_micro_usd(self, usage: TokenUsage) -> int:
        # USD per million tokens is numerically micro-USD per token.
        return round(
            usage.input_tokens * self.input_usd_per_mtok
            + usage.output_tokens * self.output_usd_per_mtok
        )


@dataclass(frozen=True, slots=True)
class AiMetering:
    """How AI use is metered on this instance, from the operator's configuration.

    `provider` names the configured backend in usage records ("none" when AI is
    off); `default_quotas` apply until an admin sets the instance quotas.
    """

    provider: str = "none"
    prices: TokenPrices = TokenPrices()
    default_quotas: InstanceQuotas = InstanceQuotas()


@dataclass(frozen=True, slots=True)
class AiUsage:
    id: str
    owner_id: str
    provider: str
    model: str
    feature: AiFeature
    input_tokens: int
    output_tokens: int
    cost_micro_usd: int
    period: str  # the billing month, e.g. "2026-11"
    created_at: datetime


@dataclass(frozen=True, slots=True)
class UserUsage:
    """One user's summed usage for a period."""

    owner_id: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_micro_usd: int
    pages: int = 0  # AI generation pages reserved against the monthly quota


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """One provider and model's summed usage for a period."""

    provider: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_micro_usd: int
