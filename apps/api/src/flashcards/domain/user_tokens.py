# SPDX-License-Identifier: AGPL-3.0-only
"""Single-use, expiring tokens for email verification and password reset (F-AUTH-2).

A token is sent by email and stored only as a hash. It is valid once, for its own
purpose, before it expires; issuing a new token of the same purpose invalidates
the user's earlier ones.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

VERIFICATION_TTL = timedelta(hours=48)
PASSWORD_RESET_TTL = timedelta(hours=1)
# A new email of the same kind is not sent within this long of the previous one,
# so neither form can be used to flood someone's inbox.
RESET_COOLDOWN = timedelta(minutes=1)
VERIFICATION_COOLDOWN = timedelta(minutes=1)


class TokenPurpose(enum.StrEnum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"  # noqa: S105 - a purpose name, not a secret


class InvalidUserToken(Exception):
    """The link is unknown, already used, expired, or for another purpose."""


@dataclass(frozen=True, slots=True)
class UserToken:
    id: str
    user_id: str
    purpose: TokenPurpose
    token_hash: str
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None

    def is_usable(self, purpose: TokenPurpose, now: datetime) -> bool:
        return self.purpose is purpose and self.used_at is None and now < self.expires_at
