# SPDX-License-Identifier: AGPL-3.0-only
"""Sign-in with failed-attempt throttling and lockout (F-AUTH-6).

Wraps AuthService.login: every attempt is counted atomically before the password
is checked, an attempt past the limit is refused, and a successful sign-in clears
the count. Only the failure that starts a lock is audited, so a guessing attack
cannot flood the audit log.
"""

from __future__ import annotations

from flashcards.application.audit_service import AuditService
from flashcards.application.auth_service import (
    AccountSuspended,
    AuthService,
    InvalidCredentials,
    IssuedSession,
)
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import hash_token, normalize_email
from flashcards.domain.login_throttle import LOCKOUT, MAX_FAILED_LOGINS, LoginLocked, admitted
from flashcards.domain.ports import Clock, LoginThrottleRepository, UserRepository


def throttle_key(email: str) -> str:
    """The throttle key for an email: a hash, so no address is stored."""
    return hash_token(f"login:{normalize_email(email)}")


class SignInService:
    def __init__(
        self,
        auth: AuthService,
        throttle: LoginThrottleRepository,
        users: UserRepository,
        audit: AuditService,
        clock: Clock,
    ) -> None:
        self._auth = auth
        self._throttle = throttle
        self._users = users
        self._audit = audit
        self._clock = clock

    async def login(self, *, email: str, password: str) -> IssuedSession:
        key = throttle_key(email)
        now = self._clock.now()
        # Count first, so attempts already in flight are counted too.
        state = await self._throttle.register_attempt(key, now)
        if not admitted(state):
            raise LoginLocked(state.locked_for(now) or LOCKOUT)
        try:
            issued = await self._auth.login(email=email, password=password)
        except InvalidCredentials:
            if state.failures == MAX_FAILED_LOGINS:  # this failure started the lock
                user = await self._users.get_by_email(normalize_email(email))
                await self._audit.record(
                    AuditAction.LOGIN_LOCKED, target_id=user.id if user is not None else None
                )
            raise
        except AccountSuspended:
            await self._throttle.delete(key)  # the password was right
            raise
        await self._throttle.delete(key)
        return issued
