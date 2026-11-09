# SPDX-License-Identifier: AGPL-3.0-only
"""Server-side session entity for cookie-based auth (F-AUTH-2).

Sessions are first-party and server-side: the cookie carries only an opaque
random token, and we store its hash, never the token itself. Revocation and
expiry are therefore authoritative on the server.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

# How stale last-seen may get before a request records it again, so an active
# session is written at most once per interval instead of on every request.
SEEN_RESOLUTION = timedelta(minutes=5)
UNKNOWN_DEVICE = "Unknown device"

# Checked in order: several browsers name others in their User-Agent (Edge says
# "Chrome" and "Safari", Chrome says "Safari"), so the most specific comes first.
_BROWSERS: tuple[tuple[str, str], ...] = (
    ("edg/", "Edge"),
    ("opr/", "Opera"),
    ("firefox/", "Firefox"),
    ("fxios/", "Firefox"),
    ("crios/", "Chrome"),
    ("chrome/", "Chrome"),
    ("safari/", "Safari"),
)
_SYSTEMS: tuple[tuple[str, str], ...] = (
    ("iphone", "iOS"),
    ("ipad", "iPadOS"),
    ("android", "Android"),
    ("cros ", "ChromeOS"),
    ("windows", "Windows"),
    ("mac os x", "macOS"),
    ("macintosh", "macOS"),
    ("linux", "Linux"),
)


def device_label(user_agent: str | None) -> str:
    """A short label such as "Firefox on macOS" for the device list.

    Only names from a fixed list can appear, so the raw User-Agent (which can
    carry arbitrary text and fingerprinting detail) is never stored or shown.
    """
    agent = (user_agent or "").lower()
    browser = next((name for marker, name in _BROWSERS if marker in agent), None)
    system = next((name for marker, name in _SYSTEMS if marker in agent), None)
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or UNKNOWN_DEVICE


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
    last_seen_at: datetime | None = None
    device: str = UNKNOWN_DEVICE

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

    def needs_touch(self, now: datetime) -> bool:
        """Whether a request at `now` should record it as the last-seen time."""
        return self.last_seen_at is None or now - self.last_seen_at >= SEEN_RESOLUTION

    def with_seen(self, now: datetime) -> Session:
        return replace(self, last_seen_at=now)
