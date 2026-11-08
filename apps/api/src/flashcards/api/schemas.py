# SPDX-License-Identifier: AGPL-3.0-only
"""Request and response models for the HTTP API.

Email is accepted as a plain string and validated in the domain
(flashcards.domain.users.validate_email), so there is one source of truth for
what a valid address is and no extra email-parsing dependency.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from flashcards.domain.decks import Deck
from flashcards.domain.invites import Invite, InviteStatus, Role
from flashcards.domain.notes import Card, Note, NoteType
from flashcards.domain.scheduling import CardSchedule, DeckStats, Rating, ScheduleState
from flashcards.domain.sessions import Session
from flashcards.domain.users import User


class SignupRequest(BaseModel):
    token: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class InviteCreateRequest(BaseModel):
    email: str | None = None
    role: Role = Role.MEMBER
    ttl_days: int = Field(default=7, gt=0, le=90)


class UserOut(BaseModel):
    id: str
    email: str
    role: Role

    @classmethod
    def from_user(cls, user: User) -> UserOut:
        return cls(id=user.id, email=user.email, role=user.role)


class CreatedInviteOut(BaseModel):
    id: str
    token: str  # shown exactly once; only its hash is stored
    role: Role
    email: str | None
    expires_at: datetime


class InviteOut(BaseModel):
    id: str
    role: Role
    email: str | None
    status: InviteStatus
    created_at: datetime
    expires_at: datetime

    @classmethod
    def from_invite(cls, invite: Invite, now: datetime) -> InviteOut:
        return cls(
            id=invite.id,
            role=invite.role,
            email=invite.email,
            status=invite.status(now),
            created_at=invite.created_at,
            expires_at=invite.expires_at,
        )


class SessionOut(BaseModel):
    id: str
    created_at: datetime
    expires_at: datetime
    current: bool

    @classmethod
    def from_session(cls, session: Session, *, current: bool) -> SessionOut:
        return cls(
            id=session.id,
            created_at=session.created_at,
            expires_at=session.expires_at,
            current=current,
        )


# --- M1: decks, cards, review, stats ---


class DeckCreateRequest(BaseModel):
    name: str
    parent_id: str | None = None


class DeckRenameRequest(BaseModel):
    name: str


class DeckMoveRequest(BaseModel):
    parent_id: str | None = None


class DeckOut(BaseModel):
    id: str
    name: str
    parent_id: str | None
    created_at: datetime

    @classmethod
    def from_deck(cls, deck: Deck) -> DeckOut:
        return cls(id=deck.id, name=deck.name, parent_id=deck.parent_id, created_at=deck.created_at)


class NoteCreateRequest(BaseModel):
    note_type: NoteType
    fields: dict[str, str]


class CardOut(BaseModel):
    id: str
    deck_id: str
    note_id: str
    ordinal: int
    suspended: bool

    @classmethod
    def from_card(cls, card: Card) -> CardOut:
        return cls(
            id=card.id,
            deck_id=card.deck_id,
            note_id=card.note_id,
            ordinal=card.ordinal,
            suspended=card.is_suspended,
        )


class CardDetailOut(CardOut):
    note_type: NoteType
    fields: dict[str, str]

    @classmethod
    def from_card_and_note(cls, card: Card, note: Note) -> CardDetailOut:
        return cls(
            id=card.id,
            deck_id=card.deck_id,
            note_id=card.note_id,
            ordinal=card.ordinal,
            suspended=card.is_suspended,
            note_type=note.note_type,
            fields=dict(note.fields),
        )


class ReviewRequest(BaseModel):
    rating: Rating
    idempotency_key: str | None = None


class CardScheduleOut(BaseModel):
    card_id: str
    state: ScheduleState
    due_at: datetime
    stability: float | None
    difficulty: float | None

    @classmethod
    def from_schedule(cls, schedule: CardSchedule) -> CardScheduleOut:
        return cls(
            card_id=schedule.card_id,
            state=schedule.state,
            due_at=schedule.due_at,
            stability=schedule.stability,
            difficulty=schedule.difficulty,
        )


class DeckStatsOut(BaseModel):
    total: int
    new: int
    learning: int
    review: int
    mature: int
    suspended: int
    due: int
    proficiency: int

    @classmethod
    def from_stats(cls, stats: DeckStats) -> DeckStatsOut:
        return cls(
            total=stats.total,
            new=stats.new,
            learning=stats.learning,
            review=stats.review,
            mature=stats.mature,
            suspended=stats.suspended,
            due=stats.due,
            proficiency=stats.proficiency,
        )
