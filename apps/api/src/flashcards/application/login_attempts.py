# SPDX-License-Identifier: AGPL-3.0-only
"""The sign-in lockout applied to second-factor codes and re-authentication.

A failed authenticator code, recovery code or passkey answer, and a wrong
password or code given to confirm a sensitive change, count against the same
per-email lockout as a wrong password (F-AUTH-6, F-AUTH-3). Each attempt is
counted before it is checked; only the failure that starts a lock is audited.
"""

from __future__ import annotations

from flashcards.application.audit_service import AuditService
from flashcards.application.sign_in_service import throttle_key
from flashcards.domain.audit import AuditAction
from flashcards.domain.login_throttle import (
    LOCKOUT,
    MAX_FAILED_LOGINS,
    LoginLocked,
    ThrottleState,
    admitted,
)
from flashcards.domain.ports import Clock, LoginThrottleRepository
from flashcards.domain.users import User


class LoginAttempts:
    def __init__(
        self, throttle: LoginThrottleRepository, audit: AuditService, clock: Clock
    ) -> None:
        self._throttle = throttle
        self._audit = audit
        self._clock = clock

    async def admit(self, user: User) -> ThrottleState:
        """Count an attempt for the user; raise LoginLocked if it is over the limit."""
        now = self._clock.now()
        state = await self._throttle.register_attempt(throttle_key(user.email), now)
        if not admitted(state):
            raise LoginLocked(state.locked_for(now) or LOCKOUT)
        return state

    async def failed(self, user: User, state: ThrottleState) -> None:
        if state.failures == MAX_FAILED_LOGINS:  # this failure started the lock
            await self._audit.record(AuditAction.LOGIN_LOCKED, target_id=user.id)

    async def succeeded(self, user: User) -> None:
        await self._throttle.delete(throttle_key(user.email))
