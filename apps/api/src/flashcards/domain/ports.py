# SPDX-License-Identifier: AGPL-3.0-only
"""Ports: interfaces the domain and application layers depend on."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from flashcards.domain.decks import Deck
from flashcards.domain.invites import Invite
from flashcards.domain.notes import Card, Note
from flashcards.domain.scheduling import CardSchedule, Rating, ReviewLog
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
    async def add(self, user: User) -> None:
        """Persist a new user.

        Raise flashcards.domain.users.EmailAlreadyRegistered if the email is
        already taken. A SQL adapter enforces this with a unique constraint and
        creates the user in the same transaction that consumes the invite.
        """
        ...

    async def get(self, user_id: str) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None: ...


class SessionRepository(Protocol):
    async def add(self, session: Session) -> None: ...

    async def get_by_token_hash(self, token_hash: str) -> Session | None: ...

    async def list_for_user(self, user_id: str) -> list[Session]:
        """Return the user's sessions, newest first, for a device list."""
        ...

    async def revoke(self, session_id: str, now: datetime) -> bool:
        """Mark an active session revoked; return whether it changed."""
        ...


class InviteRepository(Protocol):
    async def add(self, invite: Invite) -> None: ...

    async def get(self, invite_id: str) -> Invite | None: ...

    async def get_by_token_hash(self, token_hash: str) -> Invite | None: ...

    async def list_all(self) -> list[Invite]:
        """Return all invites, newest first, for the admin console."""
        ...

    async def try_redeem(self, invite_id: str, now: datetime) -> bool:
        """Atomically mark the invite redeemed.

        Return True only for the single caller that performed the transition, so two
        concurrent signups can never both consume the same invite. A SQL adapter
        implements this as one conditional UPDATE ... WHERE redeemed_at IS NULL.
        """
        ...

    async def try_revoke(self, invite_id: str, now: datetime) -> bool:
        """Mark an unredeemed, unrevoked invite revoked; return whether it changed."""
        ...


class DeckRepository(Protocol):
    async def add(self, deck: Deck) -> None: ...

    async def get(self, deck_id: str) -> Deck | None: ...

    async def list_for_owner(self, owner_id: str) -> list[Deck]:
        """Return the owner's decks, oldest first."""
        ...

    async def update(self, deck: Deck) -> None:
        """Persist a rename or move of an existing deck."""
        ...

    async def delete(self, deck_id: str) -> None: ...


class NoteRepository(Protocol):
    async def add(self, note: Note) -> None: ...

    async def get(self, note_id: str) -> Note | None: ...

    async def delete(self, note_id: str) -> None: ...

    async def delete_for_deck(self, deck_id: str) -> None:
        """Delete every note in a deck."""
        ...


class CardRepository(Protocol):
    async def add(self, card: Card) -> None: ...

    async def get(self, card_id: str) -> Card | None: ...

    async def list_for_deck(self, deck_id: str) -> list[Card]:
        """Return the deck's cards, oldest first."""
        ...

    async def update(self, card: Card) -> None:
        """Persist a card change (suspend/unsuspend)."""
        ...

    async def delete_for_note(self, note_id: str) -> None:
        """Delete every card produced by a note."""
        ...

    async def delete_for_deck(self, deck_id: str) -> None:
        """Delete every card in a deck."""
        ...


class Scheduler(Protocol):
    def new_schedule(self, card_id: str, now: datetime) -> CardSchedule:
        """Return the initial schedule for a never-reviewed card."""
        ...

    def review(self, schedule: CardSchedule, rating: Rating, now: datetime) -> CardSchedule:
        """Return the next schedule after grading a card."""
        ...


class ScheduleRepository(Protocol):
    async def get(self, card_id: str) -> CardSchedule | None: ...

    async def upsert(self, schedule: CardSchedule) -> None: ...

    async def delete(self, card_id: str) -> None:
        """Delete a card's schedule, if any."""
        ...


class ReviewLogRepository(Protocol):
    async def append(self, log: ReviewLog) -> None:
        """Append a review log entry.

        When the log carries an idempotency_key and one already exists for the
        same (owner_id, card_id, idempotency_key), raise
        flashcards.domain.scheduling.DuplicateReview instead of appending, so a
        retried submission is an atomic no-op, never a double count (NF-REL-1).
        """
        ...

    async def delete_for_card(self, card_id: str) -> None:
        """Delete every review log for a card."""
        ...
