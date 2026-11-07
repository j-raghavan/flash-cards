# SPDX-License-Identifier: AGPL-3.0-only
"""Invite-only user registration use case (F-AUTH-1, F-AUTH-2, F-AUTH-4)."""

from __future__ import annotations

import uuid

from flashcards.application.invite_service import InviteService
from flashcards.domain.ports import Clock, PasswordHasher, UserRepository
from flashcards.domain.users import (
    EmailAlreadyRegistered,
    User,
    validate_email,
    validate_password,
)


class RegistrationService:
    """Creates an account only in exchange for a valid, single-use invite.

    There is no public registration path: without an invite there is no way to
    produce a User.
    """

    def __init__(
        self,
        invites: InviteService,
        users: UserRepository,
        hasher: PasswordHasher,
        clock: Clock,
    ) -> None:
        self._invites = invites
        self._users = users
        self._hasher = hasher
        self._clock = clock

    def register(self, *, raw_token: str, email: str, password: str) -> User:
        """Redeem the invite and create the user, returning the new account.

        Validates input before touching the invite so a weak password or bad
        email never consumes a token. On success the invite is consumed exactly
        once and the user inherits the invite's role.

        M0 limitation: the invite is consumed and the user inserted in two
        steps. A unique-email race after consumption is possible in the
        in-memory adapter; the SQL adapter closes it by doing both writes in one
        transaction.
        """
        normalized_email = validate_email(email)
        validate_password(password)
        if self._users.get_by_email(normalized_email) is not None:
            raise EmailAlreadyRegistered(normalized_email)

        invite = self._invites.redeem(raw_token, email=normalized_email)

        user = User(
            id=str(uuid.uuid4()),
            email=normalized_email,
            password_hash=self._hasher.hash(password),
            role=invite.role,
            created_at=self._clock.now(),
        )
        self._users.add(user)
        return user
