# SPDX-License-Identifier: AGPL-3.0-only
"""SQLAlchemy async engine, table models and row/domain mappers.

The ORM rows live here in the adapter layer; the domain entities stay free of
SQLAlchemy. Repositories (adapters.sql) translate between the two.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from flashcards.domain.decks import Deck
from flashcards.domain.invites import Invite, Role
from flashcards.domain.notes import Card, Note, NoteType
from flashcards.domain.scheduling import CardSchedule, ReviewLog, ScheduleState
from flashcards.domain.sessions import Session
from flashcards.domain.users import User


class Base(DeclarativeBase):
    pass


class InviteRow(Base):
    __tablename__ = "invites"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    role: Mapped[str] = mapped_column(String)
    created_by: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def invite_to_row(invite: Invite) -> InviteRow:
    return InviteRow(
        id=invite.id,
        token_hash=invite.token_hash,
        role=invite.role.value,
        created_by=invite.created_by,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        email=invite.email,
        revoked_at=invite.revoked_at,
        redeemed_at=invite.redeemed_at,
    )


def invite_from_row(row: InviteRow) -> Invite:
    return Invite(
        id=row.id,
        token_hash=row.token_hash,
        role=Role(row.role),
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
        email=row.email,
        revoked_at=row.revoked_at,
        redeemed_at=row.redeemed_at,
    )


def user_to_row(user: User) -> UserRow:
    return UserRow(
        id=user.id,
        email=user.email,
        password_hash=user.password_hash,
        role=user.role.value,
        created_at=user.created_at,
        email_verified_at=user.email_verified_at,
        suspended_at=user.suspended_at,
    )


def user_from_row(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        role=Role(row.role),
        created_at=row.created_at,
        email_verified_at=row.email_verified_at,
        suspended_at=row.suspended_at,
    )


def session_to_row(session: Session) -> SessionRow:
    return SessionRow(
        id=session.id,
        user_id=session.user_id,
        token_hash=session.token_hash,
        created_at=session.created_at,
        expires_at=session.expires_at,
        revoked_at=session.revoked_at,
    )


def session_from_row(row: SessionRow) -> Session:
    return Session(
        id=row.id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


class DeckRow(Base):
    __tablename__ = "decks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    parent_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NoteRow(Base):
    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    deck_id: Mapped[str] = mapped_column(
        String, ForeignKey("decks.id", ondelete="CASCADE"), index=True
    )
    note_type: Mapped[str] = mapped_column(String)
    fields: Mapped[dict[str, str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CardRow(Base):
    __tablename__ = "cards"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    deck_id: Mapped[str] = mapped_column(
        String, ForeignKey("decks.id", ondelete="CASCADE"), index=True
    )
    note_id: Mapped[str] = mapped_column(
        String, ForeignKey("notes.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CardScheduleRow(Base):
    __tablename__ = "card_schedule"

    card_id: Mapped[str] = mapped_column(
        String, ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[str] = mapped_column(String)
    step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stability: Mapped[float | None] = mapped_column(Float, nullable=True)
    difficulty: Mapped[float | None] = mapped_column(Float, nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ReviewLogRow(Base):
    __tablename__ = "review_logs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "card_id", "idempotency_key", name="uq_review_owner_card_idempotency"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    card_id: Mapped[str] = mapped_column(
        String, ForeignKey("cards.id", ondelete="CASCADE"), index=True
    )
    rating: Mapped[int] = mapped_column(Integer)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)


def deck_to_row(deck: Deck) -> DeckRow:
    return DeckRow(
        id=deck.id,
        owner_id=deck.owner_id,
        name=deck.name,
        parent_id=deck.parent_id,
        created_at=deck.created_at,
    )


def deck_from_row(row: DeckRow) -> Deck:
    return Deck(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        parent_id=row.parent_id,
        created_at=row.created_at,
    )


def note_to_row(note: Note) -> NoteRow:
    return NoteRow(
        id=note.id,
        owner_id=note.owner_id,
        deck_id=note.deck_id,
        note_type=note.note_type.value,
        fields=dict(note.fields),
        created_at=note.created_at,
    )


def note_from_row(row: NoteRow) -> Note:
    return Note(
        id=row.id,
        owner_id=row.owner_id,
        deck_id=row.deck_id,
        note_type=NoteType(row.note_type),
        fields=tuple(row.fields.items()),
        created_at=row.created_at,
    )


def card_to_row(card: Card) -> CardRow:
    return CardRow(
        id=card.id,
        owner_id=card.owner_id,
        deck_id=card.deck_id,
        note_id=card.note_id,
        ordinal=card.ordinal,
        created_at=card.created_at,
        suspended_at=card.suspended_at,
    )


def card_from_row(row: CardRow) -> Card:
    return Card(
        id=row.id,
        owner_id=row.owner_id,
        deck_id=row.deck_id,
        note_id=row.note_id,
        ordinal=row.ordinal,
        created_at=row.created_at,
        suspended_at=row.suspended_at,
    )


def schedule_to_row(schedule: CardSchedule) -> CardScheduleRow:
    return CardScheduleRow(
        card_id=schedule.card_id,
        state=schedule.state.value,
        step=schedule.step,
        stability=schedule.stability,
        difficulty=schedule.difficulty,
        due_at=schedule.due_at,
        last_reviewed_at=schedule.last_reviewed_at,
    )


def schedule_from_row(row: CardScheduleRow) -> CardSchedule:
    return CardSchedule(
        card_id=row.card_id,
        state=ScheduleState(row.state),
        step=row.step,
        stability=row.stability,
        difficulty=row.difficulty,
        due_at=row.due_at,
        last_reviewed_at=row.last_reviewed_at,
    )


def review_log_to_row(log: ReviewLog) -> ReviewLogRow:
    return ReviewLogRow(
        id=log.id,
        owner_id=log.owner_id,
        card_id=log.card_id,
        rating=int(log.rating),
        reviewed_at=log.reviewed_at,
        idempotency_key=log.idempotency_key,
    )


def normalize_async_url(database_url: str) -> str:
    """Coerce a plain Postgres URL to the asyncpg driver.

    Hosted providers hand out ``postgresql://`` (or ``postgres://``); the async
    engine needs ``postgresql+asyncpg://``. A URL that already names a driver is
    left untouched.
    """
    if database_url.startswith("postgresql+"):
        return database_url
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+asyncpg://" + database_url[len(prefix) :]
    return database_url


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(normalize_async_url(database_url), pool_pre_ping=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
