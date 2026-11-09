# SPDX-License-Identifier: AGPL-3.0-only
"""Jobs a stopped process left queued or running are picked up at startup (ADR 0017)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.adapters.memory import (
    InMemoryAiJobRepository,
    InMemoryAuditRepository,
    InMemoryDeckRepository,
    InMemoryJobQueue,
    InMemoryObjectStore,
    InMemoryQuotaRepository,
    InMemoryQuotaSettingsRepository,
    InMemorySpendHoldRepository,
    InMemoryUsageRepository,
    InMemoryUserRepository,
)
from flashcards.api import app as app_module
from flashcards.api.app import create_app
from flashcards.application.ai_spend import AiSpend
from flashcards.application.audit_service import AuditService
from flashcards.application.deck_service import DeckService
from flashcards.application.generation_request_service import GenerationRequestService
from flashcards.application.quota_service import QuotaService
from flashcards.config import Settings
from flashcards.domain.ai import Thoroughness
from flashcards.domain.jobs import AiJob, JobFailure, JobStatus, new_generation_job
from flashcards.domain.ports import AiJobRepository
from flashcards.domain.quotas import InstanceQuotas
from flashcards.domain.usage import TokenPrices

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


class _NoExtractor:
    def sniff(self, data: bytes) -> str:
        raise AssertionError("not used")

    async def count_pages(self, data: bytes) -> int:
        raise AssertionError("not used")

    def extract(self, data: bytes, *, file_id: str, max_pages: int) -> object:
        raise AssertionError("not used")


def _job() -> AiJob:
    return new_generation_job(
        owner_id="alice",
        source_id="src",
        draft_deck_id="deck",
        thoroughness=Thoroughness.STANDARD,
        now=NOW,
    )


def _service(
    jobs: InMemoryAiJobRepository, queue: InMemoryJobQueue
) -> GenerationRequestService:
    clock = _Clock()
    usage = InMemoryUsageRepository()
    return GenerationRequestService(
        DeckService(InMemoryDeckRepository(), clock),
        _NoExtractor(),  # type: ignore[arg-type]
        InMemoryObjectStore(),
        InMemoryQuotaRepository(),
        jobs,
        queue,
        clock,
        QuotaService(
            InMemoryQuotaSettingsRepository(),
            InMemoryQuotaRepository(),
            usage,
            InMemoryUserRepository(),
            AuditService(InMemoryAuditRepository(), clock),
            clock,
            defaults=InstanceQuotas(),
        ),
        AiSpend(
            usage,
            InMemoryQuotaSettingsRepository(),
            clock,
            InMemorySpendHoldRepository(usage),
            provider="test",
            prices=TokenPrices(),
            defaults=InstanceQuotas(),
        ),
    )


async def _add(jobs: AiJobRepository, status: JobStatus) -> AiJob:
    job = _job()
    await jobs.add(job)
    if status is JobStatus.QUEUED:
        return job
    running = await jobs.try_claim(job.id, NOW)
    assert running is not None
    if status is JobStatus.RUNNING:
        return running
    finished = running.succeeded(NOW)
    await jobs.update(finished)
    return finished


async def test_recovery_requeues_running_and_re_enqueues_queued_jobs() -> None:
    jobs, queue = InMemoryAiJobRepository(), InMemoryJobQueue()
    queued = await _add(jobs, JobStatus.QUEUED)
    running = await _add(jobs, JobStatus.RUNNING)
    done = await _add(jobs, JobStatus.SUCCEEDED)

    assert await _service(jobs, queue).recover_interrupted() == 2

    assert sorted(queue.enqueued) == sorted([queued.id, running.id])
    requeued = await jobs.get(running.id)
    assert requeued is not None and requeued.status is JobStatus.QUEUED
    assert requeued.attempt == running.attempt + 1
    finished = await jobs.get(done.id)
    assert finished is not None and finished.status is JobStatus.SUCCEEDED


async def test_a_job_that_moved_on_is_not_requeued() -> None:
    jobs = InMemoryAiJobRepository()
    running = await _add(jobs, JobStatus.RUNNING)
    assert await jobs.try_requeue_interrupted(running.id, running.attempt + 1, NOW) is None
    assert await jobs.try_requeue_interrupted("missing", 1, NOW) is None
    await jobs.update(running.succeeded(NOW))
    assert await jobs.try_requeue_interrupted(running.id, running.attempt, NOW) is None


class _FinishesFirst(InMemoryAiJobRepository):
    """A job that another process finishes between the listing and the requeue."""

    async def try_requeue_interrupted(
        self, job_id: str, attempt: int, now: datetime
    ) -> AiJob | None:
        job = await self.get(job_id)
        assert job is not None
        await self.update(job.succeeded(now))
        return await super().try_requeue_interrupted(job_id, attempt, now)


async def test_a_job_finished_during_recovery_is_not_enqueued() -> None:
    jobs, queue = _FinishesFirst(), InMemoryJobQueue()
    await _add(jobs, JobStatus.RUNNING)
    assert await _service(jobs, queue).recover_interrupted() == 0
    assert queue.enqueued == []


async def test_nothing_to_recover() -> None:
    queue = InMemoryJobQueue()
    assert await _service(InMemoryAiJobRepository(), queue).recover_interrupted() == 0
    assert queue.enqueued == []


def _app() -> FastAPI:
    return create_app(Settings(environment="test", session_cookie_secure=False))


def test_startup_resumes_a_job_left_running(caplog: pytest.LogCaptureFixture) -> None:
    app = _app()
    jobs = app.state.container.generation._jobs
    running = asyncio.run(_add(jobs, JobStatus.RUNNING))

    with caplog.at_level(logging.INFO, logger="flashcards"), TestClient(app):
        # The worker picks it up again; the source is missing, so it fails safely.
        for _ in range(200):
            job = asyncio.run(jobs.get(running.id))
            if job is not None and job.is_terminal:
                break
            asyncio.run(asyncio.sleep(0.01))

    assert job is not None and job.status is JobStatus.FAILED
    assert job.attempt == running.attempt + 1
    assert job.error == JobFailure.INTERNAL.value
    assert "requeued 1 interrupted generation job(s)" in caplog.text


def test_a_recovery_failure_does_not_stop_startup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    app = _app()

    async def broken() -> int:
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(app.state.container.generation, "recover_interrupted", broken)
    with caplog.at_level(logging.ERROR, logger="flashcards"), TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
    assert "could not recover interrupted generation jobs" in caplog.text


async def test_the_sweep_skips_jobs_touched_recently() -> None:
    jobs, queue = InMemoryAiJobRepository(), InMemoryJobQueue()
    await _add(jobs, JobStatus.QUEUED)
    running = await _add(jobs, JobStatus.RUNNING)
    service = _service(jobs, queue)

    # Both were touched at NOW, which is "now" for the service: neither is idle.
    idle = timedelta(minutes=1)
    assert await service.recover_interrupted(queued_idle=idle, running_idle=idle) == 0
    assert queue.enqueued == []
    still = await jobs.get(running.id)
    assert still is not None and still.status is JobStatus.RUNNING


async def test_a_job_out_of_attempts_is_failed_not_enqueued() -> None:
    from dataclasses import replace

    jobs, queue = InMemoryAiJobRepository(), InMemoryJobQueue()
    job = replace(_job(), max_attempts=1)
    await jobs.add(job)
    assert await jobs.try_claim(job.id, NOW) is not None

    assert await _service(jobs, queue).recover_interrupted() == 0

    assert queue.enqueued == []
    failed = await jobs.get(job.id)
    assert failed is not None and failed.status is JobStatus.FAILED
    assert failed.error == JobFailure.INTERNAL.value


def test_the_periodic_sweep_picks_up_left_behind_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "JOB_SWEEP_SECONDS", 0.01)
    monkeypatch.setattr(app_module, "QUEUED_IDLE", timedelta(0))
    app = _app()
    generation: GenerationRequestService = app.state.container.generation
    jobs = generation._jobs
    calls: list[tuple[timedelta, timedelta]] = []
    real = generation.recover_interrupted

    async def spy(*, queued_idle: timedelta, running_idle: timedelta) -> int:
        calls.append((queued_idle, running_idle))
        return await real(queued_idle=queued_idle, running_idle=running_idle)

    monkeypatch.setattr(generation, "recover_interrupted", spy)
    with TestClient(app):
        # A job left QUEUED after startup, as an old instance hands it back.
        queued = asyncio.run(_add(jobs, JobStatus.QUEUED))
        for _ in range(300):
            job = asyncio.run(jobs.get(queued.id))
            if job is not None and job.is_terminal:
                break
            asyncio.run(asyncio.sleep(0.01))

    assert job is not None and job.is_terminal  # the sweep handed it to the worker
    assert calls[0] == (timedelta(0), timedelta(0))  # startup: everything
    assert calls[1] == (timedelta(0), app_module.RUNNING_IDLE)  # the sweep
