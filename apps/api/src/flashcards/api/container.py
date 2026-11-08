# SPDX-License-Identifier: AGPL-3.0-only
"""Composition root: the one place that knows which adapters back the services.

Swapping the in-memory adapters for the Postgres ones is a change to the
providers only; nothing in the API routes or the application services moves.
"""

from __future__ import annotations

from dataclasses import dataclass

from flashcards.adapters.fsrs_scheduler import FsrsScheduler
from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryInviteRepository,
    InMemoryNoteRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
    InMemorySessionRepository,
    InMemoryUserRepository,
    SystemClock,
)
from flashcards.adapters.passwords import Argon2PasswordHasher
from flashcards.application.auth_service import AuthService
from flashcards.application.deck_service import DeckService
from flashcards.application.invite_service import InviteService
from flashcards.application.library_service import LibraryService
from flashcards.application.note_service import NoteService
from flashcards.application.registration_service import RegistrationService
from flashcards.application.review_service import ReviewService
from flashcards.domain.ports import (
    CardRepository,
    Clock,
    DeckRepository,
    InviteRepository,
    NoteRepository,
    PasswordHasher,
    ReviewLogRepository,
    Scheduler,
    ScheduleRepository,
    SessionRepository,
    UserRepository,
)


@dataclass(frozen=True, slots=True)
class Repositories:
    clock: Clock
    hasher: PasswordHasher
    scheduler: Scheduler
    invites: InviteRepository
    users: UserRepository
    sessions: SessionRepository
    decks: DeckRepository
    notes: NoteRepository
    cards: CardRepository
    schedules: ScheduleRepository
    review_logs: ReviewLogRepository
    dummy_hash: str | None = None


@dataclass(frozen=True, slots=True)
class Container:
    clock: Clock
    invite_repo: InviteRepository
    user_repo: UserRepository
    invites: InviteService
    registration: RegistrationService
    auth: AuthService
    decks: DeckService
    notes: NoteService
    reviews: ReviewService
    library: LibraryService


def build_services(repos: Repositories) -> Container:
    """Wire the application services over a set of repositories."""
    invites = InviteService(repos.invites, repos.clock)
    registration = RegistrationService(invites, repos.users, repos.hasher, repos.clock)
    auth = AuthService(
        repos.users, repos.sessions, repos.hasher, repos.clock, dummy_hash=repos.dummy_hash
    )
    decks = DeckService(repos.decks, repos.clock)
    notes = NoteService(decks, repos.notes, repos.cards, repos.clock)
    reviews = ReviewService(notes, repos.schedules, repos.review_logs, repos.scheduler, repos.clock)
    library = LibraryService(
        decks,
        notes,
        repos.decks,
        repos.notes,
        repos.cards,
        repos.schedules,
        repos.review_logs,
    )
    return Container(
        clock=repos.clock,
        invite_repo=repos.invites,
        user_repo=repos.users,
        invites=invites,
        registration=registration,
        auth=auth,
        decks=decks,
        notes=notes,
        reviews=reviews,
        library=library,
    )


def build_container() -> Container:
    """Build an in-memory container (default for dev and tests)."""
    return build_services(
        Repositories(
            clock=SystemClock(),
            hasher=Argon2PasswordHasher(),
            scheduler=FsrsScheduler(),
            invites=InMemoryInviteRepository(),
            users=InMemoryUserRepository(),
            sessions=InMemorySessionRepository(),
            decks=InMemoryDeckRepository(),
            notes=InMemoryNoteRepository(),
            cards=InMemoryCardRepository(),
            schedules=InMemoryScheduleRepository(),
            review_logs=InMemoryReviewLogRepository(),
        )
    )
