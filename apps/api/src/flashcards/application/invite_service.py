# SPDX-License-Identifier: AGPL-3.0-only
"""Use cases for creating, redeeming and revoking invites."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from flashcards.domain.invites import (
    Invite,
    InviteAlreadyRedeemed,
    InviteNotFound,
    Role,
    hash_token,
    normalize_email,
)
from flashcards.domain.ports import Clock, InviteRepository

DEFAULT_TTL = timedelta(days=7)
TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class CreatedInvite:
    invite: Invite
    raw_token: str  # shown once to the admin; never stored


class InviteService:
    def __init__(self, repo: InviteRepository, clock: Clock) -> None:
        self._repo = repo
        self._clock = clock

    async def create(
        self,
        *,
        created_by: str,
        role: Role = Role.MEMBER,
        email: str | None = None,
        ttl: timedelta = DEFAULT_TTL,
    ) -> CreatedInvite:
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        now = self._clock.now()
        raw_token = secrets.token_urlsafe(TOKEN_BYTES)
        invite = Invite(
            id=str(uuid.uuid4()),
            token_hash=hash_token(raw_token),
            role=role,
            created_by=created_by,
            created_at=now,
            expires_at=now + ttl,
            email=normalize_email(email) if email else None,
        )
        await self._repo.add(invite)
        return CreatedInvite(invite=invite, raw_token=raw_token)

    async def redeem(self, raw_token: str, email: str | None = None) -> Invite:
        """Consume an invite and return it so the caller can create the user.

        Raises a specific InviteError subclass on failure. Callers at the API
        boundary must not reveal which one to the client.
        """
        now = self._clock.now()
        invite = await self._repo.get_by_token_hash(hash_token(raw_token))
        if invite is None:
            raise InviteNotFound()
        invite.check_redeemable(now, email)
        if not await self._repo.try_redeem(invite.id, now):
            # Lost a race with another signup using the same token.
            raise InviteAlreadyRedeemed(invite.id)
        return invite.with_redeemed(now)

    async def revoke(self, invite_id: str) -> bool:
        return await self._repo.try_revoke(invite_id, self._clock.now())
