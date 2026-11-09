# SPDX-License-Identifier: AGPL-3.0-only
"""Request and response models for the HTTP API.

Email is accepted as a plain string and validated in the domain
(flashcards.domain.users.validate_email), so there is one source of truth for
what a valid address is and no extra email-parsing dependency.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints, model_validator

from flashcards.api.note_type_schemas import CardTemplateOut, NoteTypeOut, OcclusionModel
from flashcards.application.account_service import AccountExport
from flashcards.application.bulk_card_service import MAX_BULK_CARDS, BulkAction
from flashcards.application.planner_service import PlanReport
from flashcards.application.review_service import ReviewOutcome, UndoOutcome
from flashcards.domain.ai import DraftCardRecord, Thoroughness
from flashcards.domain.anki import ImportReport, UnsupportedKind
from flashcards.domain.audit import AuditEvent
from flashcards.domain.browser import BrowserRow, CardState
from flashcards.domain.chat import ChatAnswer
from flashcards.domain.decks import Deck
from flashcards.domain.evaluation import AnswerEvaluation
from flashcards.domain.goals import Goal
from flashcards.domain.invites import Invite, InviteStatus, Role
from flashcards.domain.jobs import AiJob, JobStatus
from flashcards.domain.mock_exam import ExamResult, MockExam
from flashcards.domain.note_types import CardTemplate
from flashcards.domain.notes import MAX_FLAG, MAX_MEDIA_ID_LEN, Card, Note, NoteType
from flashcards.domain.planner import ChangeReason, PlanChange, StudyPlan
from flashcards.domain.progress import (
    MAX_TIME_ZONE_LENGTH,
    DailyProgress,
    ProgressStats,
    StudySettings,
)
from flashcards.domain.scheduling import CardSchedule, DeckStats, Rating, ScheduleState
from flashcards.domain.sessions import Session
from flashcards.domain.sharing import SharedDeckPreview, ShareLink
from flashcards.domain.summary import DeckSummary
from flashcards.domain.tags import MAX_TAG_LENGTH, MAX_TAGS_PER_NOTE
from flashcards.domain.users import User


class SignupRequest(BaseModel):
    token: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class PasswordResetRequest(BaseModel):
    email: str = Field(max_length=320)


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    password: str = Field(max_length=1_000)


class InviteCreateRequest(BaseModel):
    email: str | None = None
    role: Role = Role.MEMBER
    ttl_days: int = Field(default=7, gt=0, le=90)


class UserOut(BaseModel):
    id: str
    email: str
    role: Role
    email_verified: bool

    @classmethod
    def from_user(cls, user: User) -> UserOut:
        return cls(
            id=user.id, email=user.email, role=user.role, email_verified=user.is_verified
        )


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


TagIn = Annotated[str, StringConstraints(max_length=MAX_TAG_LENGTH)]


class NoteCreateRequest(BaseModel):
    note_type: NoteType
    fields: dict[str, str]
    tags: list[TagIn] = Field(default_factory=list, max_length=MAX_TAGS_PER_NOTE)
    note_type_id: str | None = Field(default=None, max_length=MAX_MEDIA_ID_LEN)  # custom
    occlusion: OcclusionModel | None = None  # image occlusion


class NoteUpdateRequest(BaseModel):
    """Any of these; the note type cannot change. Masks apply to image occlusion notes."""

    fields: dict[str, str] | None = None
    tags: list[TagIn] | None = Field(default=None, max_length=MAX_TAGS_PER_NOTE)
    occlusion: OcclusionModel | None = None


class CardOut(BaseModel):
    id: str
    deck_id: str
    note_id: str
    ordinal: int
    suspended: bool
    flag: int
    buried_until: datetime | None

    @classmethod
    def from_card(cls, card: Card) -> CardOut:
        return cls(
            id=card.id,
            deck_id=card.deck_id,
            note_id=card.note_id,
            ordinal=card.ordinal,
            suspended=card.is_suspended,
            flag=card.flag,
            buried_until=card.buried_until,
        )


class CardDetailOut(CardOut):
    """A card with what a client needs to render it (ADR 0013).

    A custom card carries its template; an image occlusion card carries the
    image and every mask, and its own masks are those whose id is its ordinal.
    """

    note_type: NoteType
    fields: dict[str, str]
    tags: list[str]
    note_type_id: str | None = None
    template: CardTemplateOut | None = None
    occlusion: OcclusionModel | None = None

    @classmethod
    def from_card_and_note(
        cls, card: Card, note: Note, template: CardTemplate | None = None
    ) -> CardDetailOut:
        return cls(
            **CardOut.from_card(card).model_dump(),
            note_type=note.note_type,
            fields=dict(note.fields),
            tags=list(note.tags),
            note_type_id=note.note_type_id,
            template=CardTemplateOut.from_template(template) if template is not None else None,
            occlusion=OcclusionModel.from_domain(note.occlusion),
        )


class BrowserRowOut(BaseModel):
    card_id: str
    note_id: str
    deck_id: str
    deck_name: str
    ordinal: int
    note_type: NoteType
    preview: str
    tags: list[str]
    flag: int
    state: CardState
    due_at: datetime | None
    proficiency: int
    created_at: datetime

    @classmethod
    def from_row(cls, row: BrowserRow) -> BrowserRowOut:
        return cls(
            card_id=row.card_id,
            note_id=row.note_id,
            deck_id=row.deck_id,
            deck_name=row.deck_name,
            ordinal=row.ordinal,
            note_type=row.note_type,
            preview=row.preview,
            tags=list(row.tags),
            flag=row.flag,
            state=row.state,
            due_at=row.due_at,
            proficiency=row.proficiency,
            created_at=row.created_at,
        )


class BulkCardRequest(BaseModel):
    card_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_CARDS)
    action: BulkAction
    deck_id: str | None = None  # move
    tags: list[TagIn] = Field(default_factory=list, max_length=MAX_TAGS_PER_NOTE)  # add/remove
    flag: int | None = Field(default=None, ge=0, le=MAX_FLAG)  # flag (0 clears)
    until: AwareDatetime | None = None  # bury; 24 hours when omitted

    @model_validator(mode="after")
    def _arguments_present(self) -> BulkCardRequest:
        if self.action is BulkAction.MOVE and self.deck_id is None:
            raise ValueError("move needs a deck_id")
        if self.action in (BulkAction.ADD_TAGS, BulkAction.REMOVE_TAGS) and not self.tags:
            raise ValueError(f"{self.action.value} needs tags")
        if self.action is BulkAction.FLAG and self.flag is None:
            raise ValueError("flag needs a flag (0 clears it)")
        return self


class BulkResultOut(BaseModel):
    cards: int
    notes: int


class BrowserPageOut(BaseModel):
    total: int
    cards: list[BrowserRowOut]


class NoteOut(BaseModel):
    id: str
    deck_id: str
    note_type: NoteType
    fields: dict[str, str]
    tags: list[str]
    created_at: datetime
    cards: list[CardOut]
    note_type_id: str | None = None
    occlusion: OcclusionModel | None = None

    @classmethod
    def from_note(cls, note: Note, cards: list[Card]) -> NoteOut:
        return cls(
            id=note.id,
            deck_id=note.deck_id,
            note_type=note.note_type,
            fields=dict(note.fields),
            tags=list(note.tags),
            created_at=note.created_at,
            cards=[CardOut.from_card(card) for card in cards],
            note_type_id=note.note_type_id,
            occlusion=OcclusionModel.from_domain(note.occlusion),
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


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)


class CitationOut(BaseModel):
    file_id: str
    page: int


class ChatAnswerOut(BaseModel):
    answer: str
    citations: list[CitationOut]

    @classmethod
    def from_answer(cls, answer: ChatAnswer) -> ChatAnswerOut:
        return cls(
            answer=answer.answer,
            citations=[CitationOut(file_id=c.file_id, page=c.page) for c in answer.citations],
        )


class AdminUserOut(BaseModel):
    id: str
    email: str
    role: Role
    email_verified: bool
    suspended: bool
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> AdminUserOut:
        return cls(
            id=user.id,
            email=user.email,
            role=user.role,
            email_verified=user.is_verified,
            suspended=user.suspended_at is not None,
            created_at=user.created_at,
        )


class RoleUpdateRequest(BaseModel):
    role: Role


class AuditEventOut(BaseModel):
    id: str
    action: str
    created_at: datetime
    actor_id: str | None
    target_id: str | None
    detail: dict[str, str]

    @classmethod
    def from_event(cls, event: AuditEvent) -> AuditEventOut:
        return cls(
            id=event.id,
            action=event.action.value,
            created_at=event.created_at,
            actor_id=event.actor_id,
            target_id=event.target_id,
            detail=dict(event.detail),
        )


class ShareCreateRequest(BaseModel):
    allow_clone: bool = False


class ShareUpdateRequest(BaseModel):
    allow_clone: bool


class ShareLinkOut(BaseModel):
    id: str
    deck_id: str
    allow_clone: bool
    created_at: datetime
    revoked_at: datetime | None

    @classmethod
    def from_link(cls, link: ShareLink) -> ShareLinkOut:
        return cls(
            id=link.id,
            deck_id=link.deck_id,
            allow_clone=link.allow_clone,
            created_at=link.created_at,
            revoked_at=link.revoked_at,
        )


class CreatedShareLinkOut(ShareLinkOut):
    # Shown once: only a hash of the token is stored.
    token: str


class SharedDeckNodeOut(BaseModel):
    name: str
    depth: int


class SharedNoteOut(BaseModel):
    deck_index: int
    note_type: NoteType
    fields: dict[str, str]


class SharedDeckOut(BaseModel):
    name: str
    decks: list[SharedDeckNodeOut]
    deck_count: int
    notes: list[SharedNoteOut]
    note_count: int
    allow_clone: bool

    @classmethod
    def from_preview(cls, preview: SharedDeckPreview) -> SharedDeckOut:
        return cls(
            name=preview.name,
            decks=[SharedDeckNodeOut(name=d.name, depth=d.depth) for d in preview.decks],
            deck_count=preview.deck_count,
            notes=[
                SharedNoteOut(deck_index=n.deck_index, note_type=n.note_type, fields=dict(n.fields))
                for n in preview.notes
            ],
            note_count=preview.note_count,
            allow_clone=preview.allow_clone,
        )


class MockExamCreateRequest(BaseModel):
    question_count: int = Field(default=10, gt=0, le=50)
    time_limit_seconds: int | None = Field(default=None, gt=0, le=86_400)


class ExamQuestionOut(BaseModel):
    id: str
    prompt: str
    topic: str


class MockExamOut(BaseModel):
    id: str
    deck_id: str
    created_at: datetime
    time_limit_seconds: int | None
    # When the timer runs out, so a client that reloads can rebuild it.
    deadline: datetime | None
    submitted_at: datetime | None
    questions: list[ExamQuestionOut]

    @classmethod
    def from_exam(cls, exam: MockExam) -> MockExamOut:
        # The reference answers are never serialized to the client.
        return cls(
            id=exam.id,
            deck_id=exam.deck_id,
            created_at=exam.created_at,
            time_limit_seconds=exam.time_limit_seconds,
            deadline=exam.deadline,
            submitted_at=exam.submitted_at,
            questions=[
                ExamQuestionOut(id=q.id, prompt=q.prompt, topic=q.topic) for q in exam.questions
            ],
        )


class ExamAnswerIn(BaseModel):
    question_id: str
    answer: str = Field(max_length=20_000)


class ExamSubmitRequest(BaseModel):
    answers: list[ExamAnswerIn] = Field(default_factory=list, max_length=200)


class TopicScoreOut(BaseModel):
    topic: str
    score: float
    question_count: int


class QuestionScoreOut(BaseModel):
    question_id: str
    score: float


class ExamResultOut(BaseModel):
    overall_score: float
    topics: list[TopicScoreOut]
    question_scores: list[QuestionScoreOut]

    @classmethod
    def from_result(cls, result: ExamResult) -> ExamResultOut:
        return cls(
            overall_score=result.overall_score,
            topics=[
                TopicScoreOut(topic=t.topic, score=t.score, question_count=t.question_count)
                for t in result.topics
            ],
            question_scores=[
                QuestionScoreOut(question_id=qs.question_id, score=qs.score)
                for qs in result.question_scores
            ],
        )


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
            error=job.public_error,
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


class YouTubeGenerateRequest(BaseModel):
    deck_id: str
    url: str = Field(min_length=1, max_length=2_000)
    thoroughness: Thoroughness = Thoroughness.STANDARD


class TranscriptGenerateRequest(BaseModel):
    deck_id: str
    # Bounded at the edge; the domain enforces the exact transcript limit.
    transcript: str = Field(min_length=1, max_length=120_000)
    thoroughness: Thoroughness = Thoroughness.STANDARD


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


class UnsupportedConstructOut(BaseModel):
    kind: UnsupportedKind
    count: int
    examples: list[str]


class ImportReportOut(BaseModel):
    decks_created: int
    notes_imported: int
    notes_skipped: int
    notes_not_selected: int
    cards_scheduled: int
    reviews_imported: int
    dry_run: bool
    unsupported: list[UnsupportedConstructOut]

    @classmethod
    def from_report(cls, report: ImportReport) -> ImportReportOut:
        return cls(
            decks_created=report.decks_created,
            notes_imported=report.notes_imported,
            notes_skipped=report.notes_skipped,
            notes_not_selected=report.notes_not_selected,
            cards_scheduled=report.cards_scheduled,
            reviews_imported=report.reviews_imported,
            dry_run=report.dry_run,
            unsupported=[
                UnsupportedConstructOut(
                    kind=item.kind, count=item.count, examples=list(item.examples)
                )
                for item in report.unsupported
            ],
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
    tags: list[str]
    created_at: datetime
    note_type_id: str | None = None
    occlusion: OcclusionModel | None = None


class CardExportOut(BaseModel):
    id: str
    deck_id: str
    note_id: str
    ordinal: int
    suspended: bool
    flag: int
    buried_until: datetime | None


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


class MockExamQuestionExportOut(BaseModel):
    prompt: str
    reference: str
    topic: str


class MockExamExportOut(BaseModel):
    id: str
    deck_id: str
    created_at: datetime
    time_limit_seconds: int | None
    submitted_at: datetime | None
    questions: list[MockExamQuestionExportOut]


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
    mock_exams: list[MockExamExportOut]
    share_links: list[ShareLinkOut]
    note_types: list[NoteTypeOut] = Field(default_factory=list)
    study_settings: StudySettingsOut | None = None

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
                    tags=list(n.tags),
                    created_at=n.created_at,
                    note_type_id=n.note_type_id,
                    occlusion=OcclusionModel.from_domain(n.occlusion),
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
                    flag=c.flag,
                    buried_until=c.buried_until,
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
            mock_exams=[
                MockExamExportOut(
                    id=e.id,
                    deck_id=e.deck_id,
                    created_at=e.created_at,
                    time_limit_seconds=e.time_limit_seconds,
                    submitted_at=e.submitted_at,
                    questions=[
                        MockExamQuestionExportOut(
                            prompt=q.prompt, reference=q.reference, topic=q.topic
                        )
                        for q in e.questions
                    ],
                )
                for e in export.mock_exams
            ],
            share_links=[ShareLinkOut.from_link(link) for link in export.share_links],
            note_types=[NoteTypeOut.from_note_type(t) for t in export.note_types],
            study_settings=(
                StudySettingsOut.from_settings(export.study_settings)
                if export.study_settings is not None
                else None
            ),
        )


class StudySettingsOut(BaseModel):
    daily_goal: int
    time_zone: str

    @classmethod
    def from_settings(cls, settings: StudySettings) -> StudySettingsOut:
        return cls(daily_goal=settings.daily_goal, time_zone=settings.time_zone)


class StudySettingsUpdateRequest(BaseModel):
    daily_goal: int | None = None
    time_zone: Annotated[str, StringConstraints(max_length=MAX_TIME_ZONE_LENGTH)] | None = None


class DailyGoalOut(BaseModel):
    date: date
    time_zone: str
    reviews: int
    daily_goal: int
    goal_met: bool
    # True exactly when the latest review reached the goal: the moment to celebrate.
    just_completed: bool

    @classmethod
    def from_progress(cls, progress: DailyProgress) -> DailyGoalOut:
        return cls(
            date=progress.day,
            time_zone=progress.time_zone,
            reviews=progress.reviews,
            daily_goal=progress.daily_goal,
            goal_met=progress.goal_met,
            just_completed=progress.just_completed,
        )


class TodayOut(DailyGoalOut):
    streak: int  # consecutive days meeting the goal, ending today or yesterday

    @classmethod
    def from_today(cls, progress: DailyProgress) -> TodayOut:
        return cls(
            **DailyGoalOut.from_progress(progress).model_dump(), streak=progress.streak or 0
        )


class ReviewResultOut(CardScheduleOut):
    review_id: str  # pass to POST /reviews/{review_id}/undo to take the answer back
    daily_goal: DailyGoalOut

    @classmethod
    def from_outcome(cls, outcome: ReviewOutcome, progress: DailyProgress) -> ReviewResultOut:
        return cls(
            **CardScheduleOut.from_schedule(outcome.schedule).model_dump(),
            review_id=outcome.review_id,
            daily_goal=DailyGoalOut.from_progress(progress),
        )


class UndoOut(BaseModel):
    review_id: str
    card_id: str
    schedule: CardScheduleOut | None  # None: the card is new again
    daily_goal: DailyGoalOut  # today's count without the undone answer

    @classmethod
    def from_outcome(cls, outcome: UndoOutcome, progress: DailyProgress) -> UndoOut:
        restored = outcome.schedule
        return cls(
            review_id=outcome.review_id,
            card_id=outcome.card_id,
            schedule=CardScheduleOut.from_schedule(restored) if restored is not None else None,
            daily_goal=DailyGoalOut.from_progress(progress),
        )


class HeatmapDayOut(BaseModel):
    date: date
    reviews: int


class RetentionPointOut(BaseModel):
    week_start: date
    reviews: int
    recalled: int
    rate: float | None


class ForecastDayOut(BaseModel):
    date: date
    due: int


class ProgressStatsOut(BaseModel):
    time_zone: str
    deck_id: str | None
    heatmap: list[HeatmapDayOut]
    retention: list[RetentionPointOut]
    forecast: list[ForecastDayOut]

    @classmethod
    def from_stats(cls, stats: ProgressStats) -> ProgressStatsOut:
        return cls(
            time_zone=stats.time_zone,
            deck_id=stats.deck_id,
            heatmap=[HeatmapDayOut(date=d.day, reviews=d.reviews) for d in stats.heatmap],
            retention=[
                RetentionPointOut(
                    week_start=p.week_start, reviews=p.reviews, recalled=p.recalled, rate=p.rate
                )
                for p in stats.retention
            ],
            forecast=[ForecastDayOut(date=d.day, due=d.due) for d in stats.forecast],
        )


class PlanChangeReasonOut(BaseModel):
    reason: ChangeReason
    count: int


class PlanChangeOut(BaseModel):
    since: datetime
    changed: bool
    daily_load_before: int
    daily_load_after: int
    before: StudyPlanOut
    reasons: list[PlanChangeReasonOut]

    @classmethod
    def from_change(cls, change: PlanChange) -> PlanChangeOut:
        return cls(
            since=change.since,
            changed=change.changed,
            daily_load_before=change.daily_load_before,
            daily_load_after=change.daily_load_after,
            before=StudyPlanOut.from_plan(change.before),
            reasons=[PlanChangeReasonOut(reason=r, count=n) for r, n in change.reasons],
        )


class PlanReportOut(StudyPlanOut):
    # What changed since the plan was last accepted; None for a goal's first plan.
    changes: PlanChangeOut | None

    @classmethod
    def from_report(cls, report: PlanReport) -> PlanReportOut:
        change = report.change
        return cls(
            **StudyPlanOut.from_plan(report.plan).model_dump(),
            changes=PlanChangeOut.from_change(change) if change is not None else None,
        )
