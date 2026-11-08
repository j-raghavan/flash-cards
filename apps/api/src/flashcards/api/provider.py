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
    SqlQuotaRepository,
    SqlReviewLogRepository,
    SqlScheduleRepository,
    SqlSessionRepository,
    SqlShareLinkRepository,
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
        async with sessionmaker() as session, session.begin():
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
                    job_queue=job_queue,
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
                    public_base_url=public_base_url,
                )
            )

    return engine, provider, job_queue
