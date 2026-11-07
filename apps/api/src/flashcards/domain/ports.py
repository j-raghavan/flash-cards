# SPDX-License-Identifier: AGPL-3.0-only
"""Ports: interfaces the domain and application layers depend on."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from flashcards.domain.invites import Invite
from flashcards.domain.sessions import Session
from flashcards.domain.users import User


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str:
        """Return a self-describing password hash (algorithm and parameters embedded)."""
        ...

    def verify(self, password_hash: str, password: str) -> bool:
        """Return whether the password matches the hash. Never raises on a mismatch."""
        ...


class UserRepository(Protocol):
    def add(self, user: User) -> None:
        """Persist a new user.

        Raise flashcards.domain.users.EmailAlreadyRegistered if the email is
        already taken. A SQL adapter enforces this with a unique constraint and
        creates the user in the same transaction that consumes the invite.
        """
        ...

    def get(self, user_id: str) -> User | None: ...

    def get_by_email(self, email: str) -> User | None: ...


class SessionRepository(Protocol):
    def add(self, session: Session) -> None: ...

    def get_by_token_hash(self, token_hash: str) -> Session | None: ...

    def list_for_user(self, user_id: str) -> list[Session]:
        """Return the user's sessions, newest first, for a device list."""
        ...

    def revoke(self, session_id: str, now: datetime) -> bool:
        """Mark an active session revoked; return whether it changed."""
        ...


class InviteRepository(Protocol):
    def add(self, invite: Invite) -> None: ...

    def get(self, invite_id: str) -> Invite | None: ...

    def get_by_token_hash(self, token_hash: str) -> Invite | None: ...

    def list_all(self) -> list[Invite]:
        """Return all invites, newest first, for the admin console."""
        ...

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
