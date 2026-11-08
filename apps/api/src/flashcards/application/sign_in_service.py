# SPDX-License-Identifier: AGPL-3.0-only
"""Sign-in with failed-attempt throttling and lockout (F-AUTH-6).

Wraps AuthService.login: a locked email is refused before the password is even
checked, a wrong password counts toward the lock, and a successful sign-in clears
the count. Only the moment a lock starts is audited, so a guessing attack cannot
flood the audit log.
"""

from __future__ import annotations

from flashcards.application.audit_service import AuditService
from flashcards.application.auth_service import AuthService, InvalidCredentials, IssuedSession
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import hash_token, normalize_email
from flashcards.domain.login_throttle import LoginLocked, after_failure
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
        state = await self._throttle.get(key)
        now = self._clock.now()
        if state is not None:
            remaining = state.locked_for(now)
            if remaining is not None:
                raise LoginLocked(remaining)
        try:
            issued = await self._auth.login(email=email, password=password)
        except InvalidCredentials:
            updated = after_failure(state, key, now)
            await self._throttle.save(updated)
            remaining = updated.locked_for(now)
            if remaining is not None:
                user = await self._users.get_by_email(normalize_email(email))
                await self._audit.record(
                    AuditAction.LOGIN_LOCKED, target_id=user.id if user is not None else None
                )
            raise
        if state is not None:
            await self._throttle.delete(key)
        return issued
