# SPDX-License-Identifier: AGPL-3.0-only
"""User entity and account rules for the invite-only identity context."""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import datetime

from flashcards.domain.invites import Role, normalize_email

# Minimum password length. Short of a breach-list check (added later), length is
# the single most effective policy, so we set a generous floor rather than a
# complexity rule that pushes users toward predictable substitutions.
MIN_PASSWORD_LENGTH = 12


class UserStatus(enum.StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class UserError(Exception):
    """Base class for user/account failures."""


class InvalidEmail(UserError):
    pass


class WeakPassword(UserError):
    pass


class EmailAlreadyRegistered(UserError):
    """Raised when creating a user whose email already exists."""


def validate_email(email: str) -> str:
    """Normalize and minimally validate an email address, returning the normalized form.

    This is a boundary sanity check, not proof of deliverability; email
    verification (F-AUTH-2) is what actually confirms the address.
    """
    normalized = normalize_email(email)
    local, sep, domain = normalized.partition("@")
    if (
        not sep
        or not local
        or "." not in domain
        or domain.startswith(".")
        or domain.endswith(".")
        or ".." in domain
    ):
        raise InvalidEmail(email)
    return normalized


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


@dataclass(frozen=True, slots=True)
class User:
    id: str
    email: str
    password_hash: str
    role: Role
    created_at: datetime
    email_verified_at: datetime | None = None
    suspended_at: datetime | None = None

    def status(self) -> UserStatus:
        if self.suspended_at is not None:
            return UserStatus.SUSPENDED
        return UserStatus.ACTIVE

    @property
    def is_verified(self) -> bool:
        return self.email_verified_at is not None

    def with_verified(self, now: datetime) -> User:
        return replace(self, email_verified_at=now)

    def with_suspended(self, now: datetime) -> User:
        return replace(self, suspended_at=now)

    def with_unsuspended(self) -> User:
        return replace(self, suspended_at=None)

    def with_password_hash(self, password_hash: str) -> User:
        return replace(self, password_hash=password_hash)

    def with_role(self, role: Role) -> User:
        return replace(self, role=role)
