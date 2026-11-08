# SPDX-License-Identifier: AGPL-3.0-only
"""Request and response models for the HTTP API.

Email is accepted as a plain string and validated in the domain
(flashcards.domain.users.validate_email), so there is one source of truth for
what a valid address is and no extra email-parsing dependency.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from flashcards.application.account_service import AccountExport
from flashcards.domain.ai import DraftCardRecord
from flashcards.domain.anki import ImportReport
from flashcards.domain.decks import Deck
from flashcards.domain.evaluation import AnswerEvaluation
from flashcards.domain.goals import Goal
from flashcards.domain.invites import Invite, InviteStatus, Role
from flashcards.domain.jobs import AiJob, JobStatus
from flashcards.domain.notes import Card, Note, NoteType
from flashcards.domain.planner import StudyPlan
from flashcards.domain.scheduling import CardSchedule, DeckStats, Rating, ScheduleState
from flashcards.domain.sessions import Session
from flashcards.domain.summary import DeckSummary
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
    name: str = Field(max_length=500)
    parent_id: str | None = None


class DeckRenameRequest(BaseModel):
    name: str = Field(max_length=500)


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


class EvaluateRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=20_000)


class EvaluationOut(BaseModel):
    verdict: str
    score: float
    explanation: str

    @classmethod
    def from_evaluation(cls, evaluation: AnswerEvaluation) -> EvaluationOut:
        return cls(
            verdict=evaluation.verdict.value,
            score=evaluation.score,
            explanation=evaluation.explanation,
        )


class DeckSummaryOut(BaseModel):
    summary: str
    key_points: list[str]

    @classmethod
    def from_summary(cls, summary: DeckSummary) -> DeckSummaryOut:
        return cls(summary=summary.summary, key_points=list(summary.key_points))


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


class JobOut(BaseModel):
    id: str
    status: JobStatus
    progress: float
    total_units: int
    completed_units: int
    cancel_requested: bool
    error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_job(cls, job: AiJob) -> JobOut:
        return cls(
            id=job.id,
            status=job.status,
            progress=job.progress,
            total_units=job.total_units,
            completed_units=job.completed_units,
            cancel_requested=job.cancel_requested,
            error=job.error,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


class DraftOut(BaseModel):
    id: str
    note_type: NoteType
    fields: dict[str, str]
    source_page: int

    @classmethod
    def from_record(cls, record: DraftCardRecord) -> DraftOut:
        return cls(
            id=record.id,
            note_type=record.note_type,
            fields=dict(record.fields),
            source_page=record.source_page,
        )


class ApproveDraftsRequest(BaseModel):
    draft_ids: list[str] = Field(min_length=1, max_length=500)


class GoalCreateRequest(BaseModel):
    name: str
    deck_ids: list[str] = Field(min_length=1)
    exam_date: datetime
    desired_grade: float = Field(ge=0.0, le=1.0)


class GoalUpdateRequest(BaseModel):
    name: str | None = None
    deck_ids: list[str] | None = Field(default=None, min_length=1)
    exam_date: datetime | None = None
    desired_grade: float | None = Field(default=None, ge=0.0, le=1.0)


class GoalOut(BaseModel):
    id: str
    name: str
    deck_ids: list[str]
    exam_date: datetime
    target_retention: float
    created_at: datetime

    @classmethod
    def from_goal(cls, goal: Goal) -> GoalOut:
        return cls(
            id=goal.id,
            name=goal.name,
            deck_ids=list(goal.deck_ids),
            exam_date=goal.exam_date,
            target_retention=goal.target_retention,
            created_at=goal.created_at,
        )


class StudyPlanOut(BaseModel):
    days_until_exam: int
    total_cards: int
    new_cards: int
    scheduled_cards: int
    new_per_day: int
    reviews_per_day: int
    readiness: float
    on_track: bool

    @classmethod
    def from_plan(cls, plan: StudyPlan) -> StudyPlanOut:
        return cls(
            days_until_exam=plan.days_until_exam,
            total_cards=plan.total_cards,
            new_cards=plan.new_cards,
            scheduled_cards=plan.scheduled_cards,
            new_per_day=plan.new_per_day,
            reviews_per_day=plan.reviews_per_day,
            readiness=plan.readiness,
            on_track=plan.on_track,
        )


class ImportReportOut(BaseModel):
    decks_created: int
    notes_imported: int
    notes_skipped: int

    @classmethod
    def from_report(cls, report: ImportReport) -> ImportReportOut:
        return cls(
            decks_created=report.decks_created,
            notes_imported=report.notes_imported,
            notes_skipped=report.notes_skipped,
        )


class UserExportOut(BaseModel):
    id: str
    email: str
    role: Role
    created_at: datetime


class DeckExportOut(BaseModel):
    id: str
    name: str
    parent_id: str | None
    created_at: datetime


class NoteExportOut(BaseModel):
    id: str
    deck_id: str
    note_type: NoteType
    fields: dict[str, str]
    created_at: datetime


class CardExportOut(BaseModel):
    id: str
    deck_id: str
    note_id: str
    ordinal: int
    suspended: bool


class ScheduleExportOut(BaseModel):
    card_id: str
    state: ScheduleState
    stability: float | None
    difficulty: float | None
    due_at: datetime
    last_reviewed_at: datetime | None


class ReviewLogExportOut(BaseModel):
    id: str
    card_id: str
    rating: int
    reviewed_at: datetime


class GoalExportOut(BaseModel):
    id: str
    name: str
    deck_ids: list[str]
    exam_date: datetime
    target_retention: float
    created_at: datetime


class MediaExportOut(BaseModel):
    id: str
    content_type: str
    created_at: datetime  # metadata only; the bytes are fetched from GET /media/{id}


class AccountExportOut(BaseModel):
    user: UserExportOut
    decks: list[DeckExportOut]
    notes: list[NoteExportOut]
    cards: list[CardExportOut]
    schedules: list[ScheduleExportOut]
    review_logs: list[ReviewLogExportOut]
    goals: list[GoalExportOut]
    media: list[MediaExportOut]

    @classmethod
    def from_export(cls, export: AccountExport) -> AccountExportOut:
        return cls(
            user=UserExportOut(
                id=export.user.id,
                email=export.user.email,
                role=export.user.role,
                created_at=export.user.created_at,
            ),
            decks=[
                DeckExportOut(
                    id=d.id, name=d.name, parent_id=d.parent_id, created_at=d.created_at
                )
                for d in export.decks
            ],
            notes=[
                NoteExportOut(
                    id=n.id,
                    deck_id=n.deck_id,
                    note_type=n.note_type,
                    fields=dict(n.fields),
                    created_at=n.created_at,
                )
                for n in export.notes
            ],
            cards=[
                CardExportOut(
                    id=c.id,
                    deck_id=c.deck_id,
                    note_id=c.note_id,
                    ordinal=c.ordinal,
                    suspended=c.is_suspended,
                )
                for c in export.cards
            ],
            schedules=[
                ScheduleExportOut(
                    card_id=s.card_id,
                    state=s.state,
                    stability=s.stability,
                    difficulty=s.difficulty,
                    due_at=s.due_at,
                    last_reviewed_at=s.last_reviewed_at,
                )
                for s in export.schedules
            ],
            review_logs=[
                ReviewLogExportOut(
                    id=r.id, card_id=r.card_id, rating=int(r.rating), reviewed_at=r.reviewed_at
                )
                for r in export.review_logs
            ],
            goals=[
                GoalExportOut(
                    id=g.id,
                    name=g.name,
                    deck_ids=list(g.deck_ids),
                    exam_date=g.exam_date,
                    target_retention=g.target_retention,
                    created_at=g.created_at,
                )
                for g in export.goals
            ],
            media=[
                MediaExportOut(id=m.id, content_type=m.content_type, created_at=m.created_at)
                for m in export.media
            ],
        )
