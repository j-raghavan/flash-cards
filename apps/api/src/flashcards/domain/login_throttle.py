# SPDX-License-Identifier: AGPL-3.0-only
"""Sign-in throttling and temporary lockout (F-AUTH-6).

Every attempt for an email is counted before its password is checked, so requests
already in flight count too: at most MAX_FAILED_LOGINS attempts are let through
per window, and the attempt that reaches the limit starts a lock. While locked,
attempts are refused (and still counted) but never extend or end the lock; it
ends only when it expires. A successful sign-in clears the count. Unknown emails
are counted the same way, so a lockout says nothing about whether an account
exists. The key is a hash of the normalized email, never the address.
"""

from __future__ import annotations

from dataclasses import dataclass
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


def after_attempt(state: ThrottleState | None, key: str, now: datetime) -> ThrottleState:
    """The state after one more sign-in attempt at `now`."""
    if state is None or _starts_fresh(state, now):
        failures, window_started_at, locked_until = 1, now, None
    else:
        failures = state.failures + 1
        window_started_at, locked_until = state.window_started_at, state.locked_until
    if locked_until is None and failures >= MAX_FAILED_LOGINS:
        locked_until = now + LOCKOUT
    return ThrottleState(
        key=key, failures=failures, window_started_at=window_started_at, locked_until=locked_until
    )


def admitted(state: ThrottleState) -> bool:
    """Whether the attempt that produced `state` may check its password."""
    return state.failures <= MAX_FAILED_LOGINS


def _starts_fresh(state: ThrottleState, now: datetime) -> bool:
    """A lock that has expired, or a window that has passed, starts counting anew."""
    if state.locked_until is not None:
        return now >= state.locked_until
    return now - state.window_started_at >= FAILURE_WINDOW
