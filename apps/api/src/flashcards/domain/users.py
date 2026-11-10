# SPDX-License-Identifier: AGPL-3.0-only
"""User entity and account rules for the invite-only identity context."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, replace
from datetime import datetime

from flashcards.domain.invites import Role, normalize_email

# Minimum password length. Short of a breach-list check (added later), length is
# the single most effective policy, so we set a generous floor rather than a
# complexity rule that pushes users toward predictable substitutions.
MIN_PASSWORD_LENGTH = 12

_ATOM = r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+"
_LOCAL_PART = re.compile(rf"{_ATOM}(?:\.{_ATOM})*")
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN = re.compile(rf"(?:{_LABEL}\.)+[a-z]{{2,63}}")


class UserStatus(enum.StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class UserError(Exception):
    """Base class for user/account failures."""


class InvalidEmail(UserError):
    pass


class WeakPassword(UserError):
    pass


class UserNotFound(UserError):
    """No user with that id."""


class AdminPasswordProtected(UserError):
    """An admin's password cannot be set by another admin; demote them first."""


class AdminSelfAction(UserError):
    """An admin tried to suspend, demote or delete their own account."""


class LastActiveAdmin(UserError):
    """The change would leave the instance with no active admin."""


class AdminRequired(UserError):
    """The caller stopped being an active admin before the change could apply."""


class EmailAlreadyRegistered(UserError):
    """Raised when creating a user whose email already exists."""


def validate_email(email: str) -> str:
    """Normalize and validate an email address, returning the normalized form.

    Accepts a plain ASCII dot-atom address (RFC 5321 limits: 64 characters before
    the @, 254 in total, domain labels of 1 to 63 characters). Anything that could
    smuggle a second recipient or a header into an email (commas, spaces, quotes,
    angle brackets, control characters) is refused. Email verification (F-AUTH-2)
    is what confirms the address actually works.
    """
    normalized = normalize_email(email)
    local, sep, domain = normalized.rpartition("@")
    if (
        not sep
        or len(normalized) > 254
        or len(local) > 64
        or _LOCAL_PART.fullmatch(local) is None
        or _DOMAIN.fullmatch(domain) is None
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
    def is_active_admin(self) -> bool:
        return self.role is Role.ADMIN and self.status() is UserStatus.ACTIVE

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
