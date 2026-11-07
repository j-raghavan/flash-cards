# SPDX-License-Identifier: AGPL-3.0-only
"""Login and session lifecycle (F-AUTH-2)."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from flashcards.domain.invites import hash_token, normalize_email
from flashcards.domain.ports import Clock, PasswordHasher, SessionRepository, UserRepository
from flashcards.domain.sessions import Session
from flashcards.domain.users import UserStatus

DEFAULT_SESSION_TTL = timedelta(days=14)
TOKEN_BYTES = 32


class AuthError(Exception):
    """Base class for authentication failures."""


class InvalidCredentials(AuthError):
    """Wrong email or password. The message deliberately does not say which."""


class AccountSuspended(AuthError):
    pass


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
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._hasher = hasher
        self._clock = clock
        self._ttl = ttl
        # A throwaway hash verified when no user matches, so an absent account and a
        # wrong password cost the same work and cannot be told apart by timing.
        self._dummy_hash = hasher.hash(secrets.token_urlsafe(TOKEN_BYTES))

    def login(self, *, email: str, password: str) -> IssuedSession:
        user = self._users.get_by_email(normalize_email(email))
        if user is None:
            self._hasher.verify(self._dummy_hash, password)
            raise InvalidCredentials()
        if not self._hasher.verify(user.password_hash, password):
            raise InvalidCredentials()
        if user.status() is UserStatus.SUSPENDED:
            raise AccountSuspended()

        now = self._clock.now()
        raw_token = secrets.token_urlsafe(TOKEN_BYTES)
        session = Session(
            id=str(uuid.uuid4()),
            user_id=user.id,
            token_hash=hash_token(raw_token),
            created_at=now,
            expires_at=now + self._ttl,
        )
        self._sessions.add(session)
        return IssuedSession(session=session, raw_token=raw_token)

    def authenticate(self, raw_token: str) -> Session | None:
        """Return the live session for a cookie token, or None if absent/expired/revoked."""
        session = self._sessions.get_by_token_hash(hash_token(raw_token))
        if session is None or not session.is_valid(self._clock.now()):
            return None
        return session

    def logout(self, raw_token: str) -> bool:
        session = self._sessions.get_by_token_hash(hash_token(raw_token))
        if session is None:
            return False
        return self._sessions.revoke(session.id, self._clock.now())

    def sessions_for(self, user_id: str) -> list[Session]:
        return self._sessions.list_for_user(user_id)

    def revoke_session(self, session_id: str) -> bool:
        return self._sessions.revoke(session_id, self._clock.now())
