# SPDX-License-Identifier: AGPL-3.0-only
"""Failed sign-in throttling and temporary lockout (F-AUTH-6).

Failures are counted per email inside a rolling window; too many locks that email
for a while, even for the right password, which caps online guessing. Unknown
emails are counted the same way, so a lockout says nothing about whether an
account exists. The key is a hash of the normalized email, never the address.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

MAX_FAILED_LOGINS = 5
FAILURE_WINDOW = timedelta(minutes=15)
LOCKOUT = timedelta(minutes=15)


class LoginLocked(Exception):
    """Too many failed sign-ins for this email; try again after `retry_after`."""

    def __init__(self, retry_after: timedelta) -> None:
        self.retry_after = retry_after
        super().__init__(f"locked for {int(retry_after.total_seconds())} more seconds")


@dataclass(frozen=True, slots=True)
class ThrottleState:
    key: str
    failures: int
    window_started_at: datetime
    locked_until: datetime | None = None

    def locked_for(self, now: datetime) -> timedelta | None:
        """How much longer the lock lasts at `now`, or None when not locked."""
        if self.locked_until is None or now >= self.locked_until:
            return None
        return self.locked_until - now


def after_failure(state: ThrottleState | None, key: str, now: datetime) -> ThrottleState:
    """The state after one more failed sign-in at `now`."""
    if state is None or now - state.window_started_at >= FAILURE_WINDOW:
        state = ThrottleState(key=key, failures=0, window_started_at=now)
    counted = replace(state, failures=state.failures + 1, locked_until=None)
    if counted.failures >= MAX_FAILED_LOGINS:
        # Lock, and start a fresh window so the next run of failures is counted anew.
        return ThrottleState(key=key, failures=0, window_started_at=now, locked_until=now + LOCKOUT)
    return counted
