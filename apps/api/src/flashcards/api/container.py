# SPDX-License-Identifier: AGPL-3.0-only
"""Composition root: the one place that knows which adapters back the services.

Swapping the in-memory adapters for the Postgres ones is a change to the
providers only; nothing in the API routes or the application services moves.
"""

from __future__ import annotations

from dataclasses import dataclass

from flashcards.adapters.apkg import ApkgReader
from flashcards.adapters.extraction import PyMuPDFExtractor
from flashcards.adapters.fsrs_scheduler import FsrsScheduler
from flashcards.adapters.job_queue import InProcessJobQueue
from flashcards.adapters.llm import NullLLMProvider
from flashcards.adapters.media import PuremagicMediaSniffer
from flashcards.adapters.memory import (
    InMemoryAiJobRepository,
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryDraftCardRepository,
    InMemoryGoalRepository,
    InMemoryInviteRepository,
    InMemoryMediaRepository,
    InMemoryMockExamRepository,
    InMemoryNoteRepository,
    InMemoryObjectStore,
    InMemoryQuotaRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
    InMemorySessionRepository,
    InMemoryUserRepository,
    SystemClock,
)
from flashcards.adapters.passwords import Argon2PasswordHasher
from flashcards.application.account_service import AccountService
from flashcards.application.apkg_import_service import ApkgImportService
from flashcards.application.auth_service import AuthService
from flashcards.application.card_generation_service import CardGenerationService
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
from flashcards.application.planner_service import PlannerService
from flashcards.application.registration_service import RegistrationService
from flashcards.application.review_service import ReviewService
from flashcards.application.summary_service import SummaryService
from flashcards.domain.mock_exam import DEFAULT_MOCK_EXAM_UNLOCK_CARDS
from flashcards.domain.ports import (
    AiJobRepository,
    CardRepository,
    Clock,
    DeckRepository,
    DocumentExtractor,
    DraftCardRepository,
    GoalRepository,
    InviteRepository,
    JobQueue,
    LLMProvider,
    MediaRepository,
    MediaTypeSniffer,
    MockExamRepository,
    NoteRepository,
    ObjectStore,
    PackageReader,
    PasswordHasher,
    QuotaRepository,
    ReviewLogRepository,
    Scheduler,
    ScheduleRepository,
    SessionRepository,
    UserRepository,
)
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
    generation: GenerationRequestService
    draft_review: DraftReviewService
    card_generation: CardGenerationService
    goal_service: GoalService
    planner: PlannerService
    apkg_import: ApkgImportService
    account: AccountService
    media: MediaService
    evaluation: EvaluationService
    summary: SummaryService
    mock_exam: MockExamService


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
    )
    goal_service = GoalService(decks, repos.goals, repos.clock)
    planner = PlannerService(goal_service, repos.cards, repos.schedules, repos.clock)
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
    )
    media = MediaService(repos.media, repos.media_sniffer, repos.clock)
    evaluation = EvaluationService(notes, repos.llm)
    summary = SummaryService(
        decks, repos.notes, repos.cards, repos.llm, unlock_cards=DEFAULT_SUMMARY_UNLOCK_CARDS
    )
    mock_exam = MockExamService(
        decks,
        repos.notes,
        repos.cards,
        repos.mock_exams,
        repos.llm,
        repos.clock,
        unlock_cards=DEFAULT_MOCK_EXAM_UNLOCK_CARDS,
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
        generation=generation,
        draft_review=draft_review,
        card_generation=card_generation,
        goal_service=goal_service,
        planner=planner,
        apkg_import=apkg_import,
        account=account,
        media=media,
        evaluation=evaluation,
        summary=summary,
        mock_exam=mock_exam,
    )


def build_container(
    *, llm: LLMProvider | None = None, job_queue: JobQueue | None = None
) -> Container:
    """Build an in-memory container (default for dev and tests).

    llm and job_queue can be supplied so a caller can inject a real LLM or share
    one worker queue; both default to the no-op/in-process implementations.
    """
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
            ai_jobs=InMemoryAiJobRepository(),
            draft_cards=InMemoryDraftCardRepository(),
            object_store=InMemoryObjectStore(),
            quotas=InMemoryQuotaRepository(),
            extractor=PyMuPDFExtractor(),
            job_queue=job_queue or InProcessJobQueue(),
            llm=llm or NullLLMProvider(),
            goals=InMemoryGoalRepository(),
            package_reader=ApkgReader(),
            media=InMemoryMediaRepository(),
            media_sniffer=PuremagicMediaSniffer(),
            mock_exams=InMemoryMockExamRepository(),
        )
    )
