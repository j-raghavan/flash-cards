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


def build_container() -> Container:
    clock = SystemClock()
    invite_repo = InMemoryInviteRepository()
    user_repo = InMemoryUserRepository()
    session_repo = InMemorySessionRepository()
    hasher = Argon2PasswordHasher()

    invites = InviteService(invite_repo, clock)
    registration = RegistrationService(invites, user_repo, hasher, clock)
    auth = AuthService(user_repo, session_repo, hasher, clock)

    return Container(
        clock=clock,
        invite_repo=invite_repo,
        user_repo=user_repo,
        session_repo=session_repo,
        invites=invites,
        registration=registration,
        auth=auth,
    )
