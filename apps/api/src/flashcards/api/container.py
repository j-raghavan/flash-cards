# SPDX-License-Identifier: AGPL-3.0-only
"""Composition root: the one place that knows which adapters back the services.

Swapping the in-memory adapters for the Postgres ones (M0 Phase 4) is a change
here only; nothing in the API routes or the application services moves.
"""

from __future__ import annotations

from dataclasses import dataclass

from flashcards.adapters.memory import (
    InMemoryInviteRepository,
    InMemorySessionRepository,
    InMemoryUserRepository,
    SystemClock,
)
from flashcards.adapters.passwords import Argon2PasswordHasher
from flashcards.application.auth_service import AuthService
from flashcards.application.invite_service import InviteService
from flashcards.application.registration_service import RegistrationService
from flashcards.domain.ports import (
    Clock,
    InviteRepository,
    PasswordHasher,
    SessionRepository,
    UserRepository,
)


@dataclass(frozen=True, slots=True)
class Container:
    clock: Clock
    invite_repo: InviteRepository
    user_repo: UserRepository
    session_repo: SessionRepository
    invites: InviteService
    registration: RegistrationService
    auth: AuthService


def build_services(
    clock: Clock,
    invite_repo: InviteRepository,
    user_repo: UserRepository,
    session_repo: SessionRepository,
    hasher: PasswordHasher,
    dummy_hash: str | None = None,
) -> Container:
    """Wire the services over a given set of repositories and clock."""
    invites = InviteService(invite_repo, clock)
    registration = RegistrationService(invites, user_repo, hasher, clock)
    auth = AuthService(user_repo, session_repo, hasher, clock, dummy_hash=dummy_hash)
    return Container(
        clock=clock,
        invite_repo=invite_repo,
        user_repo=user_repo,
        session_repo=session_repo,
        invites=invites,
        registration=registration,
        auth=auth,
    )


def build_container() -> Container:
    """Build an in-memory container (default for dev and tests)."""
    return build_services(
        SystemClock(),
        InMemoryInviteRepository(),
        InMemoryUserRepository(),
        InMemorySessionRepository(),
        Argon2PasswordHasher(),
    )
