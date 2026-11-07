# SPDX-License-Identifier: AGPL-3.0-only
"""In-memory adapters for tests and local experiments. Not for production."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from flashcards.domain.invites import Invite
from flashcards.domain.users import EmailAlreadyRegistered, User


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class InMemoryInviteRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, Invite] = {}
        self._lock = threading.Lock()

    def add(self, invite: Invite) -> None:
        with self._lock:
            self._by_id[invite.id] = invite

    def get(self, invite_id: str) -> Invite | None:
        with self._lock:
            return self._by_id.get(invite_id)

    def get_by_token_hash(self, token_hash: str) -> Invite | None:
        with self._lock:
            for invite in self._by_id.values():
                if invite.token_hash == token_hash:
                    return invite
        return None

    def try_redeem(self, invite_id: str, now: datetime) -> bool:
        with self._lock:
            invite = self._by_id.get(invite_id)
            if invite is None or invite.redeemed_at is not None or invite.revoked_at is not None:
                return False
            self._by_id[invite_id] = invite.with_redeemed(now)
            return True

    def try_revoke(self, invite_id: str, now: datetime) -> bool:
        with self._lock:
            invite = self._by_id.get(invite_id)
            if invite is None or invite.redeemed_at is not None or invite.revoked_at is not None:
                return False
            self._by_id[invite_id] = invite.with_revoked(now)
            return True


class InMemoryUserRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, User] = {}
        self._email_to_id: dict[str, str] = {}
        self._lock = threading.Lock()

    def add(self, user: User) -> None:
        with self._lock:
            if user.email in self._email_to_id:
                raise EmailAlreadyRegistered(user.email)
            self._by_id[user.id] = user
            self._email_to_id[user.email] = user.id

    def get(self, user_id: str) -> User | None:
        with self._lock:
            return self._by_id.get(user_id)

    def get_by_email(self, email: str) -> User | None:
        with self._lock:
            user_id = self._email_to_id.get(email)
            return self._by_id.get(user_id) if user_id is not None else None
