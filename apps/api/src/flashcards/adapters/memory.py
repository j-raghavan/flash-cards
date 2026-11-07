# SPDX-License-Identifier: AGPL-3.0-only
"""In-memory adapters for tests and local experiments. Not for production."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from flashcards.domain.invites import Invite


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
