# SPDX-License-Identifier: AGPL-3.0-only
"""Service providers: how a Container is produced per request.

In-memory mode yields one shared Container. SQL mode yields a Container bound to
a fresh AsyncSession inside a transaction, so each request is one unit of work
that commits on success and rolls back on any error.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Callable

from sqlalchemy.ext.asyncio import AsyncEngine

from flashcards.adapters.apkg import ApkgReader
from flashcards.adapters.db import create_engine, create_sessionmaker
from flashcards.adapters.extraction import PyMuPDFExtractor
from flashcards.adapters.fsrs_scheduler import FsrsScheduler
from flashcards.adapters.job_queue import InProcessJobQueue
from flashcards.adapters.llm import NullLLMProvider
from flashcards.adapters.mailers import LogMailer
from flashcards.adapters.media import PuremagicMediaSniffer
from flashcards.adapters.memory import SystemClock
from flashcards.adapters.passwords import Argon2PasswordHasher
from flashcards.adapters.sql import (
    SqlAiJobRepository,
    SqlAuditRepository,
    SqlCardRepository,
    SqlDeckRepository,
    SqlDraftCardRepository,
    SqlGoalRepository,
    SqlInviteRepository,
    SqlLoginThrottleRepository,
    SqlMediaRepository,
    SqlMockExamRepository,
    SqlNoteRepository,
    SqlObjectStore,
    SqlPassageRepository,
    SqlPlanBaselineRepository,
    SqlQuotaRepository,
    SqlReviewLogRepository,
    SqlScheduleRepository,
    SqlSessionRepository,
    SqlShareLinkRepository,
    SqlStudySettingsRepository,
    SqlStudyStatsRepository,
    SqlUnitOfWork,
    SqlUserRepository,
    SqlUserTokenRepository,
)
from flashcards.adapters.transcripts import NullTranscriptProvider, TranscriptAwareExtractor
from flashcards.api.container import (
    Container,
    Repositories,
    build_container,
    build_services,
)
from flashcards.domain.ports import LLMProvider, Mailer
from flashcards.domain.quotas import DEFAULT_DAILY_AI_CALLS, DEFAULT_DAILY_CLONE_CARDS

ServicesProvider = Callable[[], AsyncIterator[Container]]


class _AfterCommitQueue:
    """Holds a unit of work's enqueued jobs until its transaction commits."""

    def __init__(self, queue: InProcessJobQueue) -> None:
        self._queue = queue
        self._pending: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self._pending.append(job_id)

    async def release(self) -> None:
        for job_id in self._pending:
            await self._queue.enqueue(job_id)
        self._pending.clear()

TOKEN_BYTES = 32


def in_memory_provider(
    llm: LLMProvider | None = None,
    *,
    mailer: Mailer | None = None,
    public_base_url: str = "http://localhost:5173",
) -> tuple[Container, ServicesProvider, InProcessJobQueue]:
    job_queue = InProcessJobQueue()
    container = build_container(
        llm=llm, job_queue=job_queue, mailer=mailer, public_base_url=public_base_url
    )

    async def provider() -> AsyncIterator[Container]:
        yield container

    return container, provider, job_queue


def sql_provider(
    database_url: str,
    llm: LLMProvider | None = None,
    *,
    mailer: Mailer | None = None,
    public_base_url: str = "http://localhost:5173",
) -> tuple[AsyncEngine, ServicesProvider, InProcessJobQueue]:
    engine = create_engine(database_url)
    sessionmaker = create_sessionmaker(engine)
    clock = SystemClock()
    hasher = Argon2PasswordHasher()
    scheduler = FsrsScheduler()
    dummy_hash = hasher.hash(secrets.token_urlsafe(TOKEN_BYTES))
    # Stateless/process-level singletons, shared across requests.
    extractor = TranscriptAwareExtractor(PyMuPDFExtractor())
    transcripts = NullTranscriptProvider()
    package_reader = ApkgReader()
    media_sniffer = PuremagicMediaSniffer()
    job_queue = InProcessJobQueue()
    provider_llm = llm or NullLLMProvider()
    provider_mailer = mailer or LogMailer(show_content=False)

    async def provider() -> AsyncIterator[Container]:
        # One unit of work: commit on success, roll back on any error. Explicit
        # commit/rollback (instead of session.begin()) lets long work such as the
        # generation worker commit progress midway through SqlUnitOfWork.
        async with sessionmaker() as session:
            after_commit = _AfterCommitQueue(job_queue)
            try:
                yield build_services(
                    Repositories(
                        clock=clock,
                        hasher=hasher,
                        scheduler=scheduler,
                        dummy_hash=dummy_hash,
                        invites=SqlInviteRepository(session),
                        users=SqlUserRepository(session),
                        sessions=SqlSessionRepository(session),
                        decks=SqlDeckRepository(session),
                        notes=SqlNoteRepository(session),
                        cards=SqlCardRepository(session),
                        schedules=SqlScheduleRepository(session),
                        review_logs=SqlReviewLogRepository(session),
                        ai_jobs=SqlAiJobRepository(session),
                        draft_cards=SqlDraftCardRepository(session),
                        object_store=SqlObjectStore(session),
                        quotas=SqlQuotaRepository(session),
                        extractor=extractor,
                        job_queue=after_commit,
                        llm=provider_llm,
                        goals=SqlGoalRepository(session),
                        package_reader=package_reader,
                        media=SqlMediaRepository(session),
                        media_sniffer=media_sniffer,
                        mock_exams=SqlMockExamRepository(session),
                        transcripts=transcripts,
                        passages=SqlPassageRepository(session),
                        # Same counter table as page quota, separate daily period keys.
                        ai_calls=SqlQuotaRepository(session, limit=DEFAULT_DAILY_AI_CALLS),
                        share_links=SqlShareLinkRepository(session),
                        clone_quota=SqlQuotaRepository(session, limit=DEFAULT_DAILY_CLONE_CARDS),
                        audit_events=SqlAuditRepository(session),
                        login_throttle=SqlLoginThrottleRepository(session),
                        user_tokens=SqlUserTokenRepository(session),
                        mailer=provider_mailer,
                        study_settings=SqlStudySettingsRepository(session),
                        plan_baselines=SqlPlanBaselineRepository(session),
                        study_stats=SqlStudyStatsRepository(session),
                        public_base_url=public_base_url,
                        unit_of_work=SqlUnitOfWork(session),
                    )
                )
            except BaseException:
                await session.rollback()
                raise
            await session.commit()
            # Jobs are handed to the worker only once they are committed, so it can
            # never look for a job its own session cannot see yet.
            await after_commit.release()

    return engine, provider, job_queue
