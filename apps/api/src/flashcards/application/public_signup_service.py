# SPDX-License-Identifier: AGPL-3.0-only
"""Public sign-up, for instances that turn invite-only off (F-AUTH-7).

The API offers this only when INVITE_ONLY is false. It keeps the protections of
the invite flow and adds two of its own, because anyone can call it:

- Attempts are counted per client address before anything else, with the same
  window and limit as sign-in (login_throttle), so it cannot be used to create
  accounts or probe addresses in bulk.
- The answer is the same whether or not the email already has an account. A new
  account gets a verification email; an existing one gets a notice instead, and
  the password is hashed either way so the timing matches.

New accounts are members with unverified email, and each is audited.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from flashcards.application.audit_service import AuditService
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import Role, hash_token
from flashcards.domain.login_throttle import LOCKOUT, LoginLocked, admitted
from flashcards.domain.ports import Clock, LoginThrottleRepository, PasswordHasher, UserRepository
from flashcards.domain.users import User, validate_email, validate_password


def signup_throttle_key(client: str) -> str:
    """The throttle key for a client address: a hash, so no address is stored."""
    return hash_token(f"signup:{client}")


@dataclass(frozen=True, slots=True)
class SignupOutcome:
    """The new account, or the one the email already had (created is False).

    The caller answers the same way for both.
    """

    user: User
    created: bool


class PublicSignupService:
    def __init__(
        self,
        users: UserRepository,
        hasher: PasswordHasher,
        throttle: LoginThrottleRepository,
        audit: AuditService,
        clock: Clock,
    ) -> None:
        self._users = users
        self._hasher = hasher
        self._throttle = throttle
        self._audit = audit
        self._clock = clock

    async def register(self, *, email: str, password: str, client: str) -> SignupOutcome:
        """Create a member account, or report the account the email already has.

        Raises LoginLocked when the client made too many attempts, and InvalidEmail
        or WeakPassword on bad input. The attempt is counted first in every case.
        """
        now = self._clock.now()
        state = await self._throttle.register_attempt(signup_throttle_key(client), now)
        if not admitted(state):
            raise LoginLocked(state.locked_for(now) or LOCKOUT)
        normalized = validate_email(email)
        validate_password(password)
        password_hash = await self._hasher.hash(password)  # before the lookup: same work either way
        existing = await self._users.get_by_email(normalized)
        if existing is not None:
            return SignupOutcome(user=existing, created=False)
        user = User(
            id=str(uuid.uuid4()),
            email=normalized,
            password_hash=password_hash,
            role=Role.MEMBER,
            created_at=now,
        )
        await self._users.add(user)
        await self._audit.record(
            AuditAction.USER_REGISTERED,
            actor_id=user.id,
            target_id=user.id,
            detail={"via": "public"},
        )
        return SignupOutcome(user=user, created=True)
