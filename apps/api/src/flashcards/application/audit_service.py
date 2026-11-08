# SPDX-License-Identifier: AGPL-3.0-only
"""Record and read the audit log (F-AUTH-5, F-ADMIN-5)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from flashcards.domain.audit import MAX_AUDIT_PAGE, AuditAction, AuditEvent
from flashcards.domain.ports import AuditRepository, Clock


class AuditService:
    def __init__(self, events: AuditRepository, clock: Clock) -> None:
        self._events = events
        self._clock = clock

    async def invites_revoked(self, invite_ids: list[str], *, actor_id: str | None) -> None:
        """Record invites revoked because their creator lost admin rights or left."""
        for invite_id in invite_ids:
            await self.record(
                AuditAction.INVITE_REVOKED,
                actor_id=actor_id,
                target_id=invite_id,
                detail={"reason": "creator removed"},
            )

    async def record(
        self,
        action: AuditAction,
        *,
        actor_id: str | None = None,
        target_id: str | None = None,
        detail: Mapping[str, str] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            id=str(uuid.uuid4()),
            action=action,
            created_at=self._clock.now(),
            actor_id=actor_id,
            target_id=target_id,
            detail=tuple(sorted((detail or {}).items())),
        )
        await self._events.append(event)
        return event

    async def recent(self, *, limit: int, before: str | None = None) -> list[AuditEvent]:
        return await self._events.list_recent(
            limit=max(1, min(limit, MAX_AUDIT_PAGE)), before=before
        )
