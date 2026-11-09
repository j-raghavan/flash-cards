# SPDX-License-Identifier: AGPL-3.0-only
"""Login and session lifecycle (F-AUTH-2)."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from flashcards.domain.invites import hash_token, normalize_email
from flashcards.domain.ports import Clock, PasswordHasher, SessionRepository, UserRepository
from flashcards.domain.sessions import UNKNOWN_DEVICE, Session
from flashcards.domain.users import User, UserStatus

DEFAULT_SESSION_TTL = timedelta(days=14)
TOKEN_BYTES = 32


class AuthError(Exception):
    """Base class for authentication failures."""


class InvalidCredentials(AuthError):
    """Wrong email or password. The message deliberately does not say which."""


class AccountSuspended(AuthError):
    pass


class EmailNotVerified(AuthError):
    """The password was right, but this instance requires a verified email first."""


@dataclass(frozen=True, slots=True)
class IssuedSession:
    session: Session
    raw_token: str  # set as the cookie value; only its hash is stored


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        sessions: SessionRepository,
        hasher: PasswordHasher,
        clock: Clock,
        ttl: timedelta = DEFAULT_SESSION_TTL,
        dummy_hash: str | None = None,
        require_verified: bool = False,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._hasher = hasher
        self._clock = clock
        self._ttl = ttl
        # On an instance with public sign-up, an address must be confirmed before
        # it can sign in, so nobody can use an account made with someone else's
        # (or a throwaway) email.
        self._require_verified = require_verified
        # A throwaway hash verified when no user matches, so an absent account and a
        # wrong password cost the same work and cannot be told apart by timing.
        # Callers that build one AuthService per request pass a precomputed value
        # so the Argon2 work happens once at startup, not on every request.
        self._dummy_hash = dummy_hash if dummy_hash is not None else hasher.hash(
            secrets.token_urlsafe(TOKEN_BYTES)
        )

    async def login(
        self, *, email: str, password: str, device: str = UNKNOWN_DEVICE
    ) -> IssuedSession:
        user = await self._users.get_by_email(normalize_email(email))
        if user is None:
            self._hasher.verify(self._dummy_hash, password)
            raise InvalidCredentials()
        if not self._hasher.verify(user.password_hash, password):
            raise InvalidCredentials()
        if user.status() is UserStatus.SUSPENDED:
            raise AccountSuspended()
        if self._require_verified and not user.is_verified:
            raise EmailNotVerified()
        return await self.start_session(user, device=device)

    async def start_session(self, user: User, *, device: str = UNKNOWN_DEVICE) -> IssuedSession:
        """Issue a session for an already-authenticated user (e.g. just after signup).

        `device` is a label from sessions.device_label, never the raw User-Agent.
        """
        now = self._clock.now()
        raw_token = secrets.token_urlsafe(TOKEN_BYTES)
        session = Session(
            id=str(uuid.uuid4()),
            user_id=user.id,
            token_hash=hash_token(raw_token),
            created_at=now,
            expires_at=now + self._ttl,
            last_seen_at=now,
            device=device,
        )
        await self._sessions.add(session)
        return IssuedSession(session=session, raw_token=raw_token)

    async def authenticate(self, raw_token: str) -> Session | None:
        """Return the live session for a cookie token, or None if absent/expired/revoked."""
        now = self._clock.now()
        session = await self._sessions.get_by_token_hash(hash_token(raw_token))
        if session is None or not session.is_valid(now):
            return None
        if session.needs_touch(now):
            await self._sessions.touch(session.id, now)
            session = session.with_seen(now)
        return session

    async def logout(self, raw_token: str) -> bool:
        session = await self._sessions.get_by_token_hash(hash_token(raw_token))
        if session is None:
            return False
        return await self._sessions.revoke(session.id, self._clock.now())

    async def sessions_for(self, user_id: str) -> list[Session]:
        return await self._sessions.list_for_user(user_id)

    async def revoke_session(self, session_id: str) -> bool:
        return await self._sessions.revoke(session_id, self._clock.now())
