# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import InMemoryAiJobRepository, InMemoryObjectStore
from flashcards.domain.ai import Thoroughness
from flashcards.domain.jobs import (
    AiJob,
    IllegalJobTransition,
    JobFailure,
    JobStatus,
    new_generation_job,
)

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def _job(max_attempts: int = 3) -> AiJob:
    return new_generation_job(
        owner_id="alice",
        source_id="src-1",
        draft_deck_id="deck-1",
        thoroughness=Thoroughness.STANDARD,
        now=NOW,
        max_attempts=max_attempts,
    )


# --- domain transitions ---


def test_new_job_is_queued_at_zero_progress() -> None:
    job = _job()
    assert job.status is JobStatus.QUEUED
    assert job.attempt == 1
    assert job.progress == 0.0
    assert not job.is_terminal
    assert not job.can_retry


def test_progress_advances_and_clamps() -> None:
    job = _job().started(NOW).with_total(2, NOW)
    job = job.advanced(NOW)
    assert job.progress == 0.5
    job = job.advanced(NOW).advanced(NOW)  # over total
    assert job.progress == 1.0


def test_terminal_transitions() -> None:
    running = _job().started(NOW)
    assert running.succeeded(NOW).is_terminal
    assert _job().cancelled(NOW).is_terminal  # cancel is allowed from QUEUED
    failed = running.failed(JobFailure.INTERNAL, NOW)
    assert failed.is_terminal
    assert failed.error == JobFailure.INTERNAL.value


def test_illegal_transitions_raise() -> None:
    queued = _job()
    with pytest.raises(IllegalJobTransition):
        queued.succeeded(NOW)  # cannot finalize a job that never ran
    with pytest.raises(IllegalJobTransition):
        queued.advanced(NOW)
    done = _job().started(NOW).succeeded(NOW)
    with pytest.raises(IllegalJobTransition):
        done.started(NOW)  # cannot restart a terminal job
    with pytest.raises(IllegalJobTransition):
        done.cancelled(NOW)
    exhausted = _job(max_attempts=1).started(NOW).failed(JobFailure.INTERNAL, NOW)
    with pytest.raises(IllegalJobTransition):
        exhausted.retried(NOW)  # the attempt cap is enforced, not advisory


def test_retry_spends_attempts_until_exhausted() -> None:
    job = _job(max_attempts=2).started(NOW).failed(JobFailure.INTERNAL, NOW)
    assert job.can_retry
    job = job.retried(NOW)
    assert job.status is JobStatus.QUEUED
    assert job.attempt == 2
    assert job.error is None
    job = job.started(NOW).failed(JobFailure.INTERNAL, NOW)
    assert not job.can_retry  # attempt == max_attempts


def test_cancel_requested_flag() -> None:
    assert _job().with_cancel_requested(NOW).cancel_requested is True


# --- in-memory object store ---


async def test_object_store_put_get_delete() -> None:
    store = InMemoryObjectStore()
    assert await store.get("k") is None
    await store.put("k", b"bytes", "application/pdf")
    assert await store.get("k") == b"bytes"
    await store.delete("k")
    assert await store.get("k") is None


# --- in-memory AiJob repository ---


async def test_job_repo_add_get_update() -> None:
    repo = InMemoryAiJobRepository()
    assert await repo.get("missing") is None
    job = _job()
    await repo.add(job)
    await repo.update(job.started(NOW).with_total(3, NOW))
    stored = await repo.get(job.id)
    assert stored is not None and stored.total_units == 3


async def test_try_claim_is_single_use() -> None:
    repo = InMemoryAiJobRepository()
    job = _job()
    await repo.add(job)
    claimed = await repo.try_claim(job.id, NOW)
    assert claimed is not None and claimed.status is JobStatus.RUNNING
    assert await repo.try_claim(job.id, NOW) is None  # already running
    assert await repo.try_claim("missing", NOW) is None


async def test_request_cancel_only_while_active() -> None:
    repo = InMemoryAiJobRepository()
    job = _job()
    await repo.add(job)
    assert await repo.request_cancel(job.id, NOW) is True
    assert await repo.request_cancel(job.id, NOW) is False  # already requested
    assert await repo.request_cancel("missing", NOW) is False
    # A terminal job cannot be cancelled.
    done = _job().started(NOW).succeeded(NOW)
    await repo.add(done)
    assert await repo.request_cancel(done.id, NOW) is False


async def test_list_running_returns_claimed_jobs() -> None:
    repo = InMemoryAiJobRepository()
    a, b = _job(), _job()
    await repo.add(a)
    await repo.add(b)
    await repo.try_claim(a.id, NOW + timedelta(seconds=1))
    assert [j.id for j in await repo.list_running()] == [a.id]


def test_public_error_never_exposes_raw_text() -> None:
    from dataclasses import replace

    job = _job()
    assert job.public_error is None
    failed = job.started(NOW).failed(JobFailure.QUOTA_EXCEEDED, NOW)
    assert failed.public_error == JobFailure.QUOTA_EXCEEDED.value
    # A row written before failures were categorized holds raw exception text.
    legacy = replace(failed, error="anthropic.APIStatusError: key sk-123 revoked")
    assert legacy.public_error == JobFailure.INTERNAL.value


def test_requeue_after_interruption_is_a_free_new_attempt() -> None:
    running = _job(max_attempts=2).started(NOW).with_total(4, NOW).advanced(NOW)
    queued = running.requeued_after_interruption(NOW)
    assert queued.status is JobStatus.QUEUED
    assert (queued.attempt, queued.max_attempts) == (2, 3)
    assert queued.completed_units == 1  # resumes where it stopped
    with pytest.raises(IllegalJobTransition):
        queued.requeued_after_interruption(NOW)
