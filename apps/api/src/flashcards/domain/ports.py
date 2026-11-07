# SPDX-License-Identifier: AGPL-3.0-only
"""Ports: interfaces the domain and application layers depend on."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from flashcards.domain.invites import Invite


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...


class InviteRepository(Protocol):
    def add(self, invite: Invite) -> None: ...

    def get(self, invite_id: str) -> Invite | None: ...

    def get_by_token_hash(self, token_hash: str) -> Invite | None: ...

    def try_redeem(self, invite_id: str, now: datetime) -> bool:
        """Atomically mark the invite redeemed.

        Return True only for the single caller that performed the transition, so two
        concurrent signups can never both consume the same invite. A SQL adapter
        implements this as one conditional UPDATE ... WHERE redeemed_at IS NULL.
        """
        ...

    def try_revoke(self, invite_id: str, now: datetime) -> bool:
        """Mark an unredeemed, unrevoked invite revoked; return whether it changed."""
        ...
