# SPDX-License-Identifier: AGPL-3.0-only
"""AI quotas and the usage report (F-ADMIN-3, F-ADMIN-4, F-AI-11).

Admins set the instance's default monthly page limit and monthly spend cap, and
per-user page limits that override the default. Every change is audited. Until
an admin sets them, the defaults come from configuration.
"""

from __future__ import annotations

from dataclasses import dataclass

from flashcards.application.audit_service import AuditService
from flashcards.domain.audit import AuditAction
from flashcards.domain.ports import (
    Clock,
    QuotaRepository,
    QuotaSettingsRepository,
    UsageRepository,
    UserRepository,
)
from flashcards.domain.quotas import InstanceQuotas, billing_period, check_monthly_pages
from flashcards.domain.usage import ProviderUsage, UserUsage
from flashcards.domain.users import UserNotFound


@dataclass(frozen=True, slots=True)
class UserQuota:
    user_id: str
    period: str
    monthly_pages: int  # the limit in force: the override, else the default
    override: int | None
    used_pages: int


@dataclass(frozen=True, slots=True)
class UsageReport:
    period: str
    cost_micro_usd: int
    spend_cap_micro_usd: int | None
    by_user: list[UserUsage]
    by_provider: list[ProviderUsage]


def _or_none(cap: int | None) -> str:
    return "none" if cap is None else str(cap)


class QuotaService:
    def __init__(
        self,
        settings: QuotaSettingsRepository,
        pages: QuotaRepository,
        usage: UsageRepository,
        users: UserRepository,
        audit: AuditService,
        clock: Clock,
        *,
        defaults: InstanceQuotas,
    ) -> None:
        self._settings = settings
        self._pages = pages
        self._usage = usage
        self._users = users
        self._audit = audit
        self._clock = clock
        self._defaults = defaults

    def current_period(self) -> str:
        return billing_period(self._clock.now())

    async def instance(self) -> InstanceQuotas:
        return await self._settings.get_instance() or self._defaults

    async def set_instance(self, *, admin_id: str, quotas: InstanceQuotas) -> InstanceQuotas:
        before = await self.instance()
        await self._settings.put_instance(quotas)
        await self._audit.record(
            AuditAction.QUOTA_DEFAULTS_CHANGED,
            actor_id=admin_id,
            detail={
                "pages_from": str(before.default_monthly_pages),
                "pages_to": str(quotas.default_monthly_pages),
                "cap_from": _or_none(before.monthly_spend_cap_micro_usd),
                "cap_to": _or_none(quotas.monthly_spend_cap_micro_usd),
            },
        )
        return quotas

    async def page_limit(self, user_id: str) -> int:
        """The user's monthly page limit: their override, else the instance default."""
        override = await self._settings.get_user_pages(user_id)
        if override is not None:
            return override
        return (await self.instance()).default_monthly_pages

    async def user_quota(self, user_id: str) -> UserQuota:
        """The user's limit and this month's use; raises UserNotFound."""
        if await self._users.get(user_id) is None:
            raise UserNotFound(user_id)
        period = self.current_period()
        override = await self._settings.get_user_pages(user_id)
        limit = (
            override if override is not None else (await self.instance()).default_monthly_pages
        )
        return UserQuota(
            user_id=user_id,
            period=period,
            monthly_pages=limit,
            override=override,
            used_pages=await self._pages.used(user_id, period),
        )

    async def set_user_pages(self, *, admin_id: str, user_id: str, pages: int | None) -> UserQuota:
        """Set or clear (None) a user's monthly page limit; raises UserNotFound."""
        if pages is not None:
            check_monthly_pages(pages)  # raises InvalidQuota
        before = await self.user_quota(user_id)
        await self._settings.set_user_pages(user_id, pages)
        await self._audit.record(
            AuditAction.QUOTA_USER_CHANGED,
            actor_id=admin_id,
            target_id=user_id,
            detail={
                "from": _or_none(before.override),
                "to": _or_none(pages),
            },
        )
        return await self.user_quota(user_id)

    async def usage_report(self, period: str) -> UsageReport:
        return UsageReport(
            period=period,
            cost_micro_usd=await self._usage.total_cost(period),
            spend_cap_micro_usd=(await self.instance()).monthly_spend_cap_micro_usd,
            by_user=await self._usage.by_user(period),
            by_provider=await self._usage.by_provider(period),
        )
