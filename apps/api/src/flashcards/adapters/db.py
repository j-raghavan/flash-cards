# SPDX-License-Identifier: AGPL-3.0-only
"""SQLAlchemy async engine, table models and row/domain mappers.

The ORM rows live here in the adapter layer; the domain entities stay free of
SQLAlchemy. Repositories (adapters.sql) translate between the two.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from flashcards.domain.ai import DraftCardRecord, Thoroughness
from flashcards.domain.audit import AuditAction, AuditEvent
from flashcards.domain.chat import Passage
from flashcards.domain.decks import Deck
from flashcards.domain.goals import Goal
from flashcards.domain.invites import Invite, Role
from flashcards.domain.jobs import AiJob, JobStatus, JobType
from flashcards.domain.login_throttle import ThrottleState
from flashcards.domain.media import MediaAsset
from flashcards.domain.mock_exam import ExamQuestion, MockExam
from flashcards.domain.notes import Card, Note, NoteType
from flashcards.domain.progress import StudySettings
from flashcards.domain.scheduling import CardSchedule, Rating, ReviewLog, ScheduleState
from flashcards.domain.sessions import Session
from flashcards.domain.sharing import ShareLink
from flashcards.domain.user_tokens import TokenPurpose, UserToken
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
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)


class CardRow(Base):
    __tablename__ = "cards"
    # One card per cloze deletion (or the single Basic card) of a note.
    __table_args__ = (UniqueConstraint("note_id", "ordinal", name="uq_cards_note_ordinal"),)

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
    flag: Mapped[int] = mapped_column(Integer, default=0)
    buried_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
        # Range reads per owner: the activity aggregates.
        Index("ix_review_logs_owner_reviewed", "owner_id", "reviewed_at"),
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
        tags=list(note.tags),
    )


def note_from_row(row: NoteRow) -> Note:
    return Note(
        id=row.id,
        owner_id=row.owner_id,
        deck_id=row.deck_id,
        note_type=NoteType(row.note_type),
        fields=tuple(row.fields.items()),
        created_at=row.created_at,
        tags=tuple(row.tags or ()),
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
        flag=card.flag,
        buried_until=card.buried_until,
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
        flag=row.flag,
        buried_until=row.buried_until,
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


def review_log_from_row(row: ReviewLogRow) -> ReviewLog:
    return ReviewLog(
        id=row.id,
        owner_id=row.owner_id,
        card_id=row.card_id,
        rating=Rating(row.rating),
        reviewed_at=row.reviewed_at,
        idempotency_key=row.idempotency_key,
    )


class AiJobRow(Base):
    __tablename__ = "ai_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, index=True)
    source_id: Mapped[str] = mapped_column(String)
    draft_deck_id: Mapped[str] = mapped_column(String)
    thoroughness: Mapped[str] = mapped_column(String)
    total_units: Mapped[int] = mapped_column(Integer)
    completed_units: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)
    max_attempts: Mapped[int] = mapped_column(Integer)
    cancel_requested: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DraftCardRow(Base):
    __tablename__ = "draft_cards"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String, ForeignKey("ai_jobs.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[str] = mapped_column(String, index=True)
    note_type: Mapped[str] = mapped_column(String)
    fields: Mapped[dict[str, str]] = mapped_column(JSON)
    source_page: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AiBlobRow(Base):
    __tablename__ = "ai_blobs"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    content_type: Mapped[str] = mapped_column(String)


class AiQuotaRow(Base):
    __tablename__ = "ai_quota"

    owner_id: Mapped[str] = mapped_column(String, primary_key=True)
    period: Mapped[str] = mapped_column(String, primary_key=True)
    used_pages: Mapped[int] = mapped_column(Integer)


def aijob_to_row(job: AiJob) -> AiJobRow:
    return AiJobRow(
        id=job.id,
        owner_id=job.owner_id,
        type=job.type.value,
        status=job.status.value,
        source_id=job.source_id,
        draft_deck_id=job.draft_deck_id,
        thoroughness=job.thoroughness.value,
        total_units=job.total_units,
        completed_units=job.completed_units,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        cancel_requested=job.cancel_requested,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def aijob_from_row(row: AiJobRow) -> AiJob:
    return AiJob(
        id=row.id,
        owner_id=row.owner_id,
        type=JobType(row.type),
        status=JobStatus(row.status),
        source_id=row.source_id,
        draft_deck_id=row.draft_deck_id,
        thoroughness=Thoroughness(row.thoroughness),
        total_units=row.total_units,
        completed_units=row.completed_units,
        attempt=row.attempt,
        max_attempts=row.max_attempts,
        cancel_requested=row.cancel_requested,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def draft_to_row(record: DraftCardRecord) -> DraftCardRow:
    return DraftCardRow(
        id=record.id,
        job_id=record.job_id,
        owner_id=record.owner_id,
        note_type=record.note_type.value,
        fields=dict(record.fields),
        source_page=record.source_page,
        created_at=record.created_at,
    )


def draft_from_row(row: DraftCardRow) -> DraftCardRecord:
    return DraftCardRecord(
        id=row.id,
        job_id=row.job_id,
        owner_id=row.owner_id,
        note_type=NoteType(row.note_type),
        fields=tuple(row.fields.items()),
        source_page=row.source_page,
        created_at=row.created_at,
    )


class GoalRow(Base):
    __tablename__ = "goals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    deck_ids: Mapped[list[str]] = mapped_column(JSON)
    exam_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    target_retention: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def goal_to_row(goal: Goal) -> GoalRow:
    return GoalRow(
        id=goal.id,
        owner_id=goal.owner_id,
        name=goal.name,
        deck_ids=list(goal.deck_ids),
        exam_date=goal.exam_date,
        target_retention=goal.target_retention,
        created_at=goal.created_at,
    )


def goal_from_row(row: GoalRow) -> Goal:
    return Goal(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        deck_ids=tuple(row.deck_ids),
        exam_date=row.exam_date,
        target_retention=row.target_retention,
        created_at=row.created_at,
    )


class MediaRow(Base):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    content_type: Mapped[str] = mapped_column(String)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def media_to_row(asset: MediaAsset) -> MediaRow:
    return MediaRow(
        id=asset.id,
        owner_id=asset.owner_id,
        content_type=asset.content_type,
        data=asset.data,
        created_at=asset.created_at,
    )


def media_from_row(row: MediaRow) -> MediaAsset:
    return MediaAsset(
        id=row.id,
        owner_id=row.owner_id,
        content_type=row.content_type,
        data=row.data,
        created_at=row.created_at,
    )


class MockExamRow(Base):
    __tablename__ = "mock_exams"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    deck_id: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The full question set, references included, as a list of flat string maps.
    questions: Mapped[list[dict[str, str]]] = mapped_column(JSON)


def mock_exam_to_row(exam: MockExam) -> MockExamRow:
    return MockExamRow(
        id=exam.id,
        owner_id=exam.owner_id,
        deck_id=exam.deck_id,
        created_at=exam.created_at,
        time_limit_seconds=exam.time_limit_seconds,
        submitted_at=exam.submitted_at,
        questions=[
            {"id": q.id, "prompt": q.prompt, "reference": q.reference, "topic": q.topic}
            for q in exam.questions
        ],
    )


def mock_exam_from_row(row: MockExamRow) -> MockExam:
    return MockExam(
        id=row.id,
        owner_id=row.owner_id,
        deck_id=row.deck_id,
        created_at=row.created_at,
        time_limit_seconds=row.time_limit_seconds,
        submitted_at=row.submitted_at,
        questions=tuple(
            ExamQuestion(
                id=q["id"], prompt=q["prompt"], reference=q["reference"], topic=q["topic"]
            )
            for q in row.questions
        ),
    )


class PassageRow(Base):
    __tablename__ = "document_passages"
    # One row per passage position: concurrent first questions cannot index twice.
    __table_args__ = (
        UniqueConstraint(
            "deck_id", "file_id", "page", "ordinal", name="uq_document_passages_position"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    deck_id: Mapped[str] = mapped_column(String, index=True)
    file_id: Mapped[str] = mapped_column(String)
    page: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


def passage_from_row(row: PassageRow) -> Passage:
    return Passage(
        id=row.id,
        owner_id=row.owner_id,
        deck_id=row.deck_id,
        file_id=row.file_id,
        page=row.page,
        ordinal=row.ordinal,
        text=row.text,
    )


class ShareLinkRow(Base):
    __tablename__ = "share_links"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    deck_id: Mapped[str] = mapped_column(String, index=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    allow_clone: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def share_link_to_row(link: ShareLink) -> ShareLinkRow:
    return ShareLinkRow(
        id=link.id,
        owner_id=link.owner_id,
        deck_id=link.deck_id,
        token_hash=link.token_hash,
        allow_clone=link.allow_clone,
        created_at=link.created_at,
        revoked_at=link.revoked_at,
    )


def share_link_from_row(row: ShareLinkRow) -> ShareLink:
    return ShareLink(
        id=row.id,
        owner_id=row.owner_id,
        deck_id=row.deck_id,
        token_hash=row.token_hash,
        allow_clone=row.allow_clone,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Assigned by the database in insertion order; pages are cut on it.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True, index=True)
    action: Mapped[str] = mapped_column(String, index=True)
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    detail: Mapped[dict[str, str]] = mapped_column(JSON)


def audit_to_row(event: AuditEvent) -> AuditEventRow:
    return AuditEventRow(
        id=event.id,
        action=event.action.value,
        actor_id=event.actor_id,
        target_id=event.target_id,
        created_at=event.created_at,
        detail=dict(event.detail),
    )


def audit_from_row(row: AuditEventRow) -> AuditEvent:
    return AuditEvent(
        id=row.id,
        action=AuditAction(row.action),
        actor_id=row.actor_id,
        target_id=row.target_id,
        created_at=row.created_at,
        detail=tuple(sorted(row.detail.items())),
    )


class LoginThrottleRow(Base):
    __tablename__ = "login_throttle"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    failures: Mapped[int] = mapped_column(Integer)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def throttle_from_row(row: LoginThrottleRow) -> ThrottleState:
    return ThrottleState(
        key=row.key,
        failures=row.failures,
        window_started_at=row.window_started_at,
        locked_until=row.locked_until,
    )


class UserTokenRow(Base):
    __tablename__ = "user_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    purpose: Mapped[str] = mapped_column(String)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def user_token_to_row(token: UserToken) -> UserTokenRow:
    return UserTokenRow(
        id=token.id,
        user_id=token.user_id,
        purpose=token.purpose.value,
        token_hash=token.token_hash,
        created_at=token.created_at,
        expires_at=token.expires_at,
        used_at=token.used_at,
    )


def user_token_from_row(row: UserTokenRow) -> UserToken:
    return UserToken(
        id=row.id,
        user_id=row.user_id,
        purpose=TokenPurpose(row.purpose),
        token_hash=row.token_hash,
        created_at=row.created_at,
        expires_at=row.expires_at,
        used_at=row.used_at,
    )


class StudySettingsRow(Base):
    __tablename__ = "study_settings"

    owner_id: Mapped[str] = mapped_column(String, primary_key=True)
    daily_goal: Mapped[int] = mapped_column(Integer)
    time_zone: Mapped[str] = mapped_column(String)


def study_settings_to_row(settings: StudySettings) -> StudySettingsRow:
    return StudySettingsRow(
        owner_id=settings.owner_id,
        daily_goal=settings.daily_goal,
        time_zone=settings.time_zone,
    )


def study_settings_from_row(row: StudySettingsRow) -> StudySettings:
    return StudySettings(
        owner_id=row.owner_id, daily_goal=row.daily_goal, time_zone=row.time_zone
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
