# SPDX-License-Identifier: AGPL-3.0-only
"""Audit log of admin and security events (F-AUTH-5, F-ADMIN-5).

Append-only: events are never edited. They carry ids and a small detail map but
no email addresses, passwords or tokens, so the log can be kept when an account
is deleted without retaining that person's personal data.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

MAX_AUDIT_PAGE = 200

Detail = tuple[tuple[str, str], ...]


class AuditAction(enum.StrEnum):
    INVITE_CREATED = "invite.created"
    INVITE_REVOKED = "invite.revoked"
    USER_SUSPENDED = "user.suspended"
    USER_UNSUSPENDED = "user.unsuspended"
    USER_ROLE_CHANGED = "user.role_changed"
    USER_DELETED = "user.deleted"
    ACCOUNT_DELETED = "account.deleted"
    USER_UNLOCKED = "user.unlocked"
    LOGIN_LOCKED = "login.locked"
    # Event names, not secrets (bandit flags any name containing PASSWORD).
    PASSWORD_RESET_REQUESTED = "password_reset.requested"  # noqa: S105
    PASSWORD_RESET_COMPLETED = "password_reset.completed"  # noqa: S105
    EMAIL_VERIFIED = "email.verified"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: str
    action: AuditAction
    created_at: datetime
    actor_id: str | None = None  # who did it, when known
    target_id: str | None = None  # what it was done to
    detail: Detail = ()
