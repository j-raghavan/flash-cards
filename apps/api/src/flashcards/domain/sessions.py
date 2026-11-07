# SPDX-License-Identifier: AGPL-3.0-only
"""Server-side session entity for cookie-based auth (F-AUTH-2).

Sessions are first-party and server-side: the cookie carries only an opaque
random token, and we store its hash, never the token itself. Revocation and
expiry are therefore authoritative on the server.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import datetime


class SessionStatus(enum.StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class Session:
    id: str
    user_id: str
    token_hash: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    def status(self, now: datetime) -> SessionStatus:
        if self.revoked_at is not None:
            return SessionStatus.REVOKED
        if now >= self.expires_at:
            return SessionStatus.EXPIRED
        return SessionStatus.ACTIVE

    def is_valid(self, now: datetime) -> bool:
        return self.status(now) is SessionStatus.ACTIVE

    def with_revoked(self, now: datetime) -> Session:
        return replace(self, revoked_at=now)
