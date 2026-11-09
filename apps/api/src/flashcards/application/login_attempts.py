# SPDX-License-Identifier: AGPL-3.0-only
"""The sign-in lockout applied to second-factor codes and re-authentication.

A failed authenticator code, recovery code or passkey answer, and a wrong
password or code given to confirm a sensitive change, count against the same
per-email lockout as a wrong password (F-AUTH-6, F-AUTH-3). Each attempt is
counted before it is checked; only the failure that starts a lock is audited.

Second-factor attempts are also counted under a per-account key that a password
reset does not clear (only a completed sign-in or an admin unlock does), so
whoever controls the mailbox cannot reset the password over and over to get
fresh code guesses.
"""

from __future__ import annotations

from flashcards.application.audit_service import AuditService
from flashcards.application.sign_in_service import throttle_key
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import hash_token
from flashcards.domain.login_throttle import (
    LOCKOUT,
    MAX_FAILED_LOGINS,
    LoginLocked,
    ThrottleState,
    admitted,
)
from flashcards.domain.ports import Clock, LoginThrottleRepository
from flashcards.domain.users import User


def second_factor_key(user_id: str) -> str:
    """The throttle key for an account's second-factor attempts."""
    return hash_token(f"second-factor:{user_id}")


class LoginAttempts:
    def __init__(
        self, throttle: LoginThrottleRepository, audit: AuditService, clock: Clock
    ) -> None:
        self._throttle = throttle
        self._audit = audit
        self._clock = clock

    async def admit(self, user: User) -> ThrottleState:
        """Count an attempt for the user; raise LoginLocked if it is over the limit.

        The returned state is the one nearer its lock, of the two keys.
        """
        now = self._clock.now()
        by_email = await self._throttle.register_attempt(throttle_key(user.email), now)
        by_account = await self._throttle.register_attempt(second_factor_key(user.id), now)
        for state in (by_email, by_account):
            if not admitted(state):
                raise LoginLocked(state.locked_for(now) or LOCKOUT)
        return max(by_email, by_account, key=lambda state: state.failures)

    async def failed(self, user: User, state: ThrottleState) -> None:
        if state.failures == MAX_FAILED_LOGINS:  # this failure started the lock
            await self._audit.record(AuditAction.LOGIN_LOCKED, target_id=user.id)

    async def succeeded(self, user: User) -> None:
        await self._throttle.delete(throttle_key(user.email))
        await self._throttle.delete(second_factor_key(user.id))
