# SPDX-License-Identifier: AGPL-3.0-only
"""Composition root: the one place that knows which adapters back the services.

Swapping the in-memory adapters for the Postgres ones is a change to the
providers only; nothing in the API routes or the application services moves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flashcards.adapters.apkg import ApkgReader
from flashcards.adapters.extraction import PyMuPDFExtractor
from flashcards.adapters.fsrs_scheduler import FsrsScheduler
from flashcards.adapters.job_queue import InProcessJobQueue
from flashcards.adapters.llm import NullLLMProvider
from flashcards.adapters.mailers import LogMailer
from flashcards.adapters.media import PuremagicMediaSniffer
from flashcards.adapters.memory import (
    InMemoryAiJobRepository,
    InMemoryAuditRepository,
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryDraftCardRepository,
    InMemoryGoalRepository,
    InMemoryInviteRepository,
    InMemoryLoginThrottleRepository,
    InMemoryMediaRepository,
    InMemoryMockExamRepository,
    InMemoryNoteRepository,
    InMemoryNoteTypeRepository,
    InMemoryObjectStore,
    InMemoryPassageRepository,
    InMemoryPlanBaselineRepository,
    InMemoryQuotaRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
    InMemorySessionRepository,
    InMemoryShareLinkRepository,
    InMemoryStudySettingsRepository,
    InMemoryStudyStatsRepository,
    InMemoryUserRepository,
    InMemoryUserTokenRepository,
    NullUnitOfWork,
    SystemClock,
)
from flashcards.adapters.passwords import Argon2PasswordHasher
from flashcards.adapters.transcripts import NullTranscriptProvider, TranscriptAwareExtractor
from flashcards.application.account_email_service import AccountEmailService
from flashcards.application.account_service import AccountService
from flashcards.application.admin_service import AdminService
from flashcards.application.ai_budget import AiCallBudget
from flashcards.application.apkg_import_service import ApkgImportService
from flashcards.application.audit_service import AuditService
from flashcards.application.auth_service import AuthService
from flashcards.application.bulk_card_service import BulkCardService
from flashcards.application.card_browser_service import CardBrowserService
from flashcards.application.card_generation_service import CardGenerationService
from flashcards.application.chat_service import ChatService
from flashcards.application.deck_service import DeckService
from flashcards.application.draft_review_service import DraftReviewService
from flashcards.application.evaluation_service import EvaluationService
from flashcards.application.generation_request_service import GenerationRequestService
from flashcards.application.goal_service import GoalService
from flashcards.application.invite_service import InviteService
from flashcards.application.library_service import LibraryService
from flashcards.application.media_service import MediaService
from flashcards.application.mock_exam_service import MockExamService
from flashcards.application.note_service import NoteService
from flashcards.application.note_type_service import NoteTypeService
from flashcards.application.planner_service import PlannerService
from flashcards.application.progress_service import ProgressService
from flashcards.application.registration_service import RegistrationService
from flashcards.application.review_service import ReviewService
from flashcards.application.sharing_service import SharingService
from flashcards.application.sign_in_service import SignInService
from flashcards.application.summary_service import SummaryService
from flashcards.application.transcript_import_service import TranscriptImportService
from flashcards.domain.mock_exam import DEFAULT_MOCK_EXAM_UNLOCK_CARDS
from flashcards.domain.ports import (
    AiJobRepository,
    AuditRepository,
    CardRepository,
    Clock,
    DeckRepository,
    DocumentExtractor,
    DraftCardRepository,
    GoalRepository,
    InviteRepository,
    JobQueue,
    LLMProvider,
    LoginThrottleRepository,
    Mailer,
    MediaRepository,
    MediaTypeSniffer,
    MockExamRepository,
    NoteRepository,
    NoteTypeRepository,
    ObjectStore,
    PackageReader,
    PassageRepository,
    PasswordHasher,
    PlanBaselineRepository,
    QuotaRepository,
    ReviewLogRepository,
    Scheduler,
    ScheduleRepository,
    SessionRepository,
    ShareLinkRepository,
    StudySettingsRepository,
    StudyStatsRepository,
    TranscriptProvider,
    UnitOfWork,
    UserRepository,
    UserTokenRepository,
)
from flashcards.domain.quotas import DEFAULT_DAILY_AI_CALLS, DEFAULT_DAILY_CLONE_CARDS
from flashcards.domain.summary import DEFAULT_SUMMARY_UNLOCK_CARDS


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
    note_types: NoteTypeRepository
    cards: CardRepository
    schedules: ScheduleRepository
    review_logs: ReviewLogRepository
    ai_jobs: AiJobRepository
    draft_cards: DraftCardRepository
    object_store: ObjectStore
    quotas: QuotaRepository
    extractor: DocumentExtractor
    job_queue: JobQueue
    llm: LLMProvider
    goals: GoalRepository
    package_reader: PackageReader
    media: MediaRepository
    media_sniffer: MediaTypeSniffer
    mock_exams: MockExamRepository
    transcripts: TranscriptProvider
    passages: PassageRepository
    ai_calls: QuotaRepository
    share_links: ShareLinkRepository
    clone_quota: QuotaRepository
    audit_events: AuditRepository
    login_throttle: LoginThrottleRepository
    user_tokens: UserTokenRepository
    mailer: Mailer
    study_settings: StudySettingsRepository
    plan_baselines: PlanBaselineRepository
    study_stats: StudyStatsRepository
    dummy_hash: str | None = None
    # Where links in emails point (the web client).
    public_base_url: str = "http://localhost:5173"
    # Lets the generation worker commit progress as it goes (ADR 0012).
    unit_of_work: UnitOfWork = field(default_factory=NullUnitOfWork)


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
    note_types: NoteTypeService
    reviews: ReviewService
    library: LibraryService
    card_browser: CardBrowserService
    bulk_cards: BulkCardService
    generation: GenerationRequestService
    draft_review: DraftReviewService
    card_generation: CardGenerationService
    goal_service: GoalService
    planner: PlannerService
    progress: ProgressService
    apkg_import: ApkgImportService
    account: AccountService
    media: MediaService
    evaluation: EvaluationService
    summary: SummaryService
    mock_exam: MockExamService
    transcript_import: TranscriptImportService
    chat: ChatService
    sharing: SharingService
    audit: AuditService
    admin: AdminService
    sign_in: SignInService
    account_email: AccountEmailService
    mailer: Mailer


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
        repos.passages,
        repos.mock_exams,
        repos.share_links,
    )
    generation = GenerationRequestService(
        decks,
        repos.extractor,
        repos.object_store,
        repos.quotas,
        repos.ai_jobs,
        repos.job_queue,
        repos.clock,
    )
    draft_review = DraftReviewService(repos.ai_jobs, repos.draft_cards, notes)
    card_generation = CardGenerationService(
        repos.ai_jobs,
        repos.draft_cards,
        repos.object_store,
        repos.extractor,
        repos.llm,
        repos.clock,
        repos.unit_of_work,
    )
    note_types = NoteTypeService(
        repos.note_types, repos.notes, notes, library, repos.media, repos.clock
    )
    goal_service = GoalService(decks, repos.goals, repos.clock)
    progress = ProgressService(decks, repos.study_settings, repos.study_stats, repos.clock)
    planner = PlannerService(
        goal_service,
        repos.cards,
        repos.schedules,
        repos.plan_baselines,
        progress,
        repos.clock,
    )
    apkg_import = ApkgImportService(repos.package_reader, decks, notes)
    account = AccountService(
        library,
        repos.decks,
        repos.notes,
        repos.cards,
        repos.schedules,
        repos.review_logs,
        repos.goals,
        repos.ai_jobs,
        repos.draft_cards,
        repos.object_store,
        repos.sessions,
        repos.users,
        repos.media,
        repos.quotas,
        repos.mock_exams,
        repos.passages,
        repos.ai_calls,
        repos.share_links,
        repos.clone_quota,
        repos.login_throttle,
        repos.user_tokens,
        invites,
        repos.note_types,
        repos.study_settings,
    )
    media = MediaService(repos.media, repos.media_sniffer, repos.clock)
    ai_budget = AiCallBudget(repos.ai_calls, repos.clock)
    evaluation = EvaluationService(notes, repos.llm, ai_budget)
    summary = SummaryService(
        decks,
        repos.notes,
        repos.cards,
        repos.llm,
        ai_budget,
        unlock_cards=DEFAULT_SUMMARY_UNLOCK_CARDS,
    )
    mock_exam = MockExamService(
        decks,
        repos.notes,
        repos.cards,
        repos.mock_exams,
        repos.llm,
        ai_budget,
        repos.clock,
        unlock_cards=DEFAULT_MOCK_EXAM_UNLOCK_CARDS,
    )
    transcript_import = TranscriptImportService(decks, generation, repos.transcripts)
    chat = ChatService(
        decks,
        repos.ai_jobs,
        repos.object_store,
        repos.extractor,
        repos.passages,
        repos.llm,
        ai_budget,
    )
    sharing = SharingService(
        decks,
        notes,
        repos.notes,
        repos.cards,
        repos.share_links,
        repos.users,
        repos.clone_quota,
        repos.clock,
        repos.note_types,
        repos.media,
    )
    audit = AuditService(repos.audit_events, repos.clock)
    admin = AdminService(
        invites,
        audit,
        repos.users,
        repos.sessions,
        account,
        repos.login_throttle,
        repos.user_tokens,
        repos.clock,
    )
    sign_in = SignInService(auth, repos.login_throttle, repos.users, audit, repos.clock)
    account_email = AccountEmailService(
        repos.users,
        repos.user_tokens,
        repos.sessions,
        repos.login_throttle,
        repos.hasher,
        audit,
        repos.clock,
        base_url=repos.public_base_url,
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
        note_types=note_types,
        reviews=reviews,
        library=library,
        card_browser=CardBrowserService(
            decks, repos.notes, repos.cards, repos.schedules, repos.clock
        ),
        bulk_cards=BulkCardService(decks, library, repos.notes, repos.cards, repos.clock),
        generation=generation,
        draft_review=draft_review,
        card_generation=card_generation,
        goal_service=goal_service,
        planner=planner,
        progress=progress,
        apkg_import=apkg_import,
        account=account,
        media=media,
        evaluation=evaluation,
        summary=summary,
        mock_exam=mock_exam,
        transcript_import=transcript_import,
        chat=chat,
        sharing=sharing,
        audit=audit,
        admin=admin,
        sign_in=sign_in,
        account_email=account_email,
        mailer=repos.mailer,
    )


def build_container(
    *,
    llm: LLMProvider | None = None,
    job_queue: JobQueue | None = None,
    transcripts: TranscriptProvider | None = None,
    mailer: Mailer | None = None,
    public_base_url: str = "http://localhost:5173",
) -> Container:
    """Build an in-memory container (default for dev and tests).

    llm and job_queue can be supplied so a caller can inject a real LLM or share
    one worker queue; both default to the no-op/in-process implementations.
    """
    cards = InMemoryCardRepository()
    schedules = InMemoryScheduleRepository()
    review_logs = InMemoryReviewLogRepository()
    goals = InMemoryGoalRepository()
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
            note_types=InMemoryNoteTypeRepository(),
            cards=cards,
            schedules=schedules,
            review_logs=review_logs,
            ai_jobs=InMemoryAiJobRepository(),
            draft_cards=InMemoryDraftCardRepository(),
            object_store=InMemoryObjectStore(),
            quotas=InMemoryQuotaRepository(),
            extractor=TranscriptAwareExtractor(PyMuPDFExtractor()),
            job_queue=job_queue or InProcessJobQueue(),
            llm=llm or NullLLMProvider(),
            goals=goals,
            package_reader=ApkgReader(),
            media=InMemoryMediaRepository(),
            media_sniffer=PuremagicMediaSniffer(),
            mock_exams=InMemoryMockExamRepository(),
            transcripts=transcripts or NullTranscriptProvider(),
            passages=InMemoryPassageRepository(),
            ai_calls=InMemoryQuotaRepository(limit=DEFAULT_DAILY_AI_CALLS),
            share_links=InMemoryShareLinkRepository(),
            clone_quota=InMemoryQuotaRepository(limit=DEFAULT_DAILY_CLONE_CARDS),
            audit_events=InMemoryAuditRepository(),
            login_throttle=InMemoryLoginThrottleRepository(),
            user_tokens=InMemoryUserTokenRepository(),
            mailer=mailer or LogMailer(show_content=True),
            study_settings=InMemoryStudySettingsRepository(),
            plan_baselines=InMemoryPlanBaselineRepository(goals),
            study_stats=InMemoryStudyStatsRepository(cards, schedules, review_logs),
            public_base_url=public_base_url,
        )
    )
