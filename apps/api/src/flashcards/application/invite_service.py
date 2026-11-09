# SPDX-License-Identifier: AGPL-3.0-only
"""Use cases for creating, redeeming and revoking invites."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote

from flashcards.domain.invites import (
    Invite,
    InviteAlreadyRedeemed,
    InviteNotFound,
    Role,
    hash_token,
    normalize_email,
)
from flashcards.domain.mail import OutgoingEmail
from flashcards.domain.ports import Clock, InviteRepository

DEFAULT_TTL = timedelta(days=7)
TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class CreatedInvite:
    invite: Invite
    raw_token: str  # shown once to the admin; never stored


class InviteHasNoEmail(Exception):
    """A link-only invite has no address to send it to."""


def invite_email(created: CreatedInvite, *, base_url: str) -> OutgoingEmail | None:
    """The email carrying an invite's link, or None for a link-only invite.

    Like the other account links, the token rides in the URL fragment, so it never
    reaches a server log or a Referer header.
    """
    if created.invite.email is None:
        return None
    link = f"{base_url.rstrip('/')}/signup#token={quote(created.raw_token)}"
    expires = created.invite.expires_at.strftime("%Y-%m-%d %H:%M UTC")
    return OutgoingEmail(
        to=created.invite.email,
        subject="You are invited to Flash-Cards",
        body=(
            "You have been invited to create a Flash-Cards account. To accept, open "
            f"this link:\n\n{link}\n\nThe invite expires on {expires}. If you were "
            "not expecting it, you can ignore this message."
        ),
    )


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

    async def rotate(self, invite_id: str) -> CreatedInvite | None:
        """Issue a new token for an active invite that has an email, voiding the old one.

        Only the token's hash is stored, so a resend cannot repeat the old link: it
        must issue a new one. The expiry is unchanged. Returns None when the invite
        is missing or no longer active; raises InviteHasNoEmail for a link-only
        invite, which has nowhere to be sent.
        """
        invite = await self._repo.get(invite_id)
        if invite is None:
            return None
        if invite.email is None:
            raise InviteHasNoEmail(invite_id)
        raw_token = secrets.token_urlsafe(TOKEN_BYTES)
        token_hash = hash_token(raw_token)
        if not await self._repo.try_rotate_token(invite_id, token_hash, self._clock.now()):
            return None
        return CreatedInvite(invite=invite.with_token_hash(token_hash), raw_token=raw_token)

    async def revoke(self, invite_id: str) -> bool:
        return await self._repo.try_revoke(invite_id, self._clock.now())

    async def revoke_created_by(self, user_id: str) -> list[str]:
        """Revoke a user's unredeemed invites, e.g. when they stop being an admin."""
        return await self._repo.revoke_created_by(user_id, self._clock.now())
