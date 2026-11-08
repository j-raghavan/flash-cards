# SPDX-License-Identifier: AGPL-3.0-only
"""Admin use cases, each recorded in the audit log (F-ADMIN-1, F-ADMIN-5).

Wraps invite management so every admin action leaves an audit event without the
underlying services needing to know about auditing.
"""

from __future__ import annotations

from datetime import timedelta

from flashcards.application.audit_service import AuditService
from flashcards.application.invite_service import DEFAULT_TTL, CreatedInvite, InviteService
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import Role


class AdminService:
    def __init__(self, invites: InviteService, audit: AuditService) -> None:
        self._invites = invites
        self._audit = audit

    async def create_invite(
        self,
        *,
        admin_id: str,
        role: Role = Role.MEMBER,
        email: str | None = None,
        ttl: timedelta = DEFAULT_TTL,
    ) -> CreatedInvite:
        created = await self._invites.create(created_by=admin_id, role=role, email=email, ttl=ttl)
        await self._audit.record(
            AuditAction.INVITE_CREATED,
            actor_id=admin_id,
            target_id=created.invite.id,
            detail={"role": role.value},
        )
        return created

    async def revoke_invite(self, *, admin_id: str, invite_id: str) -> bool:
        revoked = await self._invites.revoke(invite_id)
        if revoked:
            await self._audit.record(
                AuditAction.INVITE_REVOKED, actor_id=admin_id, target_id=invite_id
            )
        return revoked
