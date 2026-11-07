# SPDX-License-Identifier: AGPL-3.0-only
"""Invite entity and rules for the invite-only signup flow."""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, replace
from datetime import datetime


class Role(enum.StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class InviteStatus(enum.StrEnum):
    ACTIVE = "active"
    REDEEMED = "redeemed"
    REVOKED = "revoked"
    EXPIRED = "expired"


class InviteError(Exception):
    """Base class for invite failures. The API maps all of them to one generic response."""


class InviteNotFound(InviteError):
    pass


class InviteExpired(InviteError):
    pass


class InviteRevoked(InviteError):
    pass


class InviteAlreadyRedeemed(InviteError):
    pass


class InviteEmailMismatch(InviteError):
    pass


def hash_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest stored in place of the raw token.

    Invite tokens are long random values, so a fast hash is appropriate here
    (unlike passwords). The raw token is never stored.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


@dataclass(frozen=True, slots=True)
class Invite:
    id: str
    token_hash: str
    role: Role
    created_by: str
    created_at: datetime
    expires_at: datetime
    email: str | None = None
    revoked_at: datetime | None = None
    redeemed_at: datetime | None = None

    def status(self, now: datetime) -> InviteStatus:
        if self.redeemed_at is not None:
            return InviteStatus.REDEEMED
        if self.revoked_at is not None:
            return InviteStatus.REVOKED
        if now >= self.expires_at:
            return InviteStatus.EXPIRED
        return InviteStatus.ACTIVE

    def check_redeemable(self, now: datetime, email: str | None) -> None:
        """Raise the specific InviteError if this invite cannot be redeemed now."""
        status = self.status(now)
        if status is InviteStatus.REDEEMED:
            raise InviteAlreadyRedeemed(self.id)
        if status is InviteStatus.REVOKED:
            raise InviteRevoked(self.id)
        if status is InviteStatus.EXPIRED:
            raise InviteExpired(self.id)
        if self.email is not None and (
            email is None or normalize_email(email) != normalize_email(self.email)
        ):
            raise InviteEmailMismatch(self.id)

    def with_redeemed(self, now: datetime) -> Invite:
        return replace(self, redeemed_at=now)

    def with_revoked(self, now: datetime) -> Invite:
        return replace(self, revoked_at=now)
