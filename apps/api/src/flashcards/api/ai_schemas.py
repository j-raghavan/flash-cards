# SPDX-License-Identifier: AGPL-3.0-only
"""Request and response models for AI quotas, usage and the data-use disclosure
(F-ADMIN-3, F-ADMIN-4, F-AI-11, F-LEGAL-2).

Costs travel as integer micro-USD (millionths of a dollar), so sums are exact.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from flashcards.application.quota_service import UsageReport, UserQuota
from flashcards.domain.disclosure import AiDisclosure
from flashcards.domain.quotas import MAX_MONTHLY_PAGES, InstanceQuotas
from flashcards.domain.usage import ProviderUsage, UserUsage

# A billing month, e.g. "2026-11".
Period = Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
# Ten million dollars a month: a sanity bound on the cap, not a recommendation.
MAX_SPEND_CAP_MICRO_USD = 10_000_000 * 1_000_000


class InstanceQuotasIn(BaseModel):
    # None means members have no monthly page limit (ADR 0027).
    default_monthly_pages: int | None = Field(ge=0, le=MAX_MONTHLY_PAGES)
    # None removes the cap.
    monthly_spend_cap_micro_usd: int | None = Field(ge=0, le=MAX_SPEND_CAP_MICRO_USD)

    def to_domain(self) -> InstanceQuotas:
        return InstanceQuotas(
            default_monthly_pages=self.default_monthly_pages,
            monthly_spend_cap_micro_usd=self.monthly_spend_cap_micro_usd,
        )


class InstanceQuotasOut(BaseModel):
    # None means members have no monthly page limit (ADR 0027).
    default_monthly_pages: int | None
    monthly_spend_cap_micro_usd: int | None

    @classmethod
    def from_domain(cls, quotas: InstanceQuotas) -> InstanceQuotasOut:
        return cls(
            default_monthly_pages=quotas.default_monthly_pages,
            monthly_spend_cap_micro_usd=quotas.monthly_spend_cap_micro_usd,
        )


class UserQuotaIn(BaseModel):
    # None removes the override, so the instance default applies.
    monthly_pages: int | None = Field(ge=0, le=MAX_MONTHLY_PAGES)


class UserQuotaOut(BaseModel):
    user_id: str
    period: str
    # The limit in force (override, else instance default); None means unlimited.
    monthly_pages: int | None
    override: int | None
    used_pages: int

    @classmethod
    def from_quota(cls, quota: UserQuota) -> UserQuotaOut:
        return cls(
            user_id=quota.user_id,
            period=quota.period,
            monthly_pages=quota.monthly_pages,
            override=quota.override,
            used_pages=quota.used_pages,
        )


class MyAiQuotaOut(BaseModel):
    period: str
    # The caller's monthly page limit; null means no limit (an admin, or a member
    # when the limit in force is unlimited).
    monthly_pages: int | None
    used_pages: int
    # Most pages one uploaded document may have; null means no limit (an admin).
    max_document_pages: int | None
    # False for an admin, or a member with no monthly page limit: their pages are
    # counted but never refused (ADR 0022, 0027).
    quota_applies: bool


class UserUsageOut(BaseModel):
    user_id: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_micro_usd: int
    # AI generation pages used against the monthly quota in the period.
    pages: int

    @classmethod
    def from_usage(cls, usage: UserUsage) -> UserUsageOut:
        return cls(
            user_id=usage.owner_id,
            calls=usage.calls,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_micro_usd=usage.cost_micro_usd,
            pages=usage.pages,
        )


class ProviderUsageOut(BaseModel):
    provider: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_micro_usd: int

    @classmethod
    def from_usage(cls, usage: ProviderUsage) -> ProviderUsageOut:
        return cls(
            provider=usage.provider,
            model=usage.model,
            calls=usage.calls,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_micro_usd=usage.cost_micro_usd,
        )


class UsageReportOut(BaseModel):
    period: str
    cost_micro_usd: int
    spend_cap_micro_usd: int | None
    by_user: list[UserUsageOut]
    by_provider: list[ProviderUsageOut]

    @classmethod
    def from_report(cls, report: UsageReport) -> UsageReportOut:
        return cls(
            period=report.period,
            cost_micro_usd=report.cost_micro_usd,
            spend_cap_micro_usd=report.spend_cap_micro_usd,
            by_user=[UserUsageOut.from_usage(u) for u in report.by_user],
            by_provider=[ProviderUsageOut.from_usage(p) for p in report.by_provider],
        )


class AiProviderOut(BaseModel):
    name: str
    terms_url: str | None


class AiDisclosureOut(BaseModel):
    ai_enabled: bool
    trains_on_user_content: bool
    statements: list[str]
    provider: AiProviderOut | None

    @classmethod
    def from_disclosure(cls, disclosure: AiDisclosure) -> AiDisclosureOut:
        provider = disclosure.provider
        return cls(
            ai_enabled=disclosure.ai_enabled,
            trains_on_user_content=disclosure.trains_on_user_content,
            statements=list(disclosure.statements),
            provider=(
                AiProviderOut(name=provider.name, terms_url=provider.terms_url)
                if provider is not None
                else None
            ),
        )
