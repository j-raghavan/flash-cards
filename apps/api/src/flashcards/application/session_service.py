# SPDX-License-Identifier: AGPL-3.0-only
"""Session management: the device list and revocation (F-AUTH-2).

Users see their active sessions with when each started, when it was last seen
and a device label, and can sign out one session or every session but the one
they are using. Revocations are audited. Tokens and their hashes never leave
the repository layer.
"""

from __future__ import annotations

from flashcards.application.audit_service import AuditService
from flashcards.domain.audit import AuditAction
from flashcards.domain.ports import Clock, SessionRepository
from flashcards.domain.sessions import Session


class SessionService:
    def __init__(self, sessions: SessionRepository, audit: AuditService, clock: Clock) -> None:
        self._sessions = sessions
        self._audit = audit
        self._clock = clock

    async def active_for(self, user_id: str) -> list[Session]:
        """The user's live sessions, newest first; revoked and expired ones are left out."""
        now = self._clock.now()
        return [s for s in await self._sessions.list_for_user(user_id) if s.is_valid(now)]

    async def revoke(self, *, user_id: str, session_id: str) -> bool:
        """Sign out one of the user's sessions; False when it is not theirs or not live."""
        owned = {s.id for s in await self.active_for(user_id)}
        if session_id not in owned:
            return False
        if not await self._sessions.revoke(session_id, self._clock.now()):
            return False  # revoked concurrently
        await self._audit.record(
            AuditAction.SESSION_REVOKED, actor_id=user_id, target_id=session_id
        )
        return True

    async def revoke_others(self, *, user_id: str, current_session_id: str) -> int:
        """Sign out every session of the user except the current one; return how many."""
        count = await self._sessions.revoke_others(
            user_id, current_session_id, self._clock.now()
        )
        if count:
            await self._audit.record(
                AuditAction.SESSIONS_REVOKED_OTHERS,
                actor_id=user_id,
                target_id=user_id,
                detail={"count": str(count)},
            )
        return count
