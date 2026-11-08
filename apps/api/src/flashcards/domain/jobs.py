# SPDX-License-Identifier: AGPL-3.0-only
"""The AI generation job: an async, resumable unit of work (F-AI-10).

The job row is the source of truth for progress, so a crash or an idle-sleep on
the free instance can be recovered: at boot, RUNNING jobs are requeued and
resume from completed_units.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, replace
from datetime import datetime

from flashcards.domain.ai import Thoroughness

DEFAULT_MAX_ATTEMPTS = 3


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(enum.StrEnum):
    CARD_GENERATION = "card_generation"


class JobError(Exception):
    pass


class JobNotFound(JobError):
    pass


class IllegalJobTransition(JobError):
    """A lifecycle transition was attempted from a state that does not allow it."""


@dataclass(frozen=True, slots=True)
class AiJob:
    id: str
    owner_id: str
    type: JobType
    status: JobStatus
    source_id: str
    draft_deck_id: str
    thoroughness: Thoroughness
    total_units: int
    completed_units: int
    attempt: int
    max_attempts: int
    cancel_requested: bool
    error: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def progress(self) -> float:
        if self.total_units <= 0:
            return 0.0
        return min(self.completed_units / self.total_units, 1.0)

    @property
    def is_terminal(self) -> bool:
        return self.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def can_retry(self) -> bool:
        return self.status is JobStatus.FAILED and self.attempt < self.max_attempts

    def _require(self, allowed: bool, what: str) -> None:
        if not allowed:
            raise IllegalJobTransition(f"cannot {what} from {self.status}")

    def started(self, now: datetime) -> AiJob:
        self._require(self.status is JobStatus.QUEUED, "start")
        return replace(self, status=JobStatus.RUNNING, updated_at=now)

    def with_total(self, total: int, now: datetime) -> AiJob:
        self._require(self.status is JobStatus.RUNNING, "set total on")
        return replace(self, total_units=total, updated_at=now)

    def advanced(self, now: datetime) -> AiJob:
        self._require(self.status is JobStatus.RUNNING, "advance")
        return replace(self, completed_units=self.completed_units + 1, updated_at=now)

    def succeeded(self, now: datetime) -> AiJob:
        self._require(self.status is JobStatus.RUNNING, "succeed")
        return replace(self, status=JobStatus.SUCCEEDED, updated_at=now)

    def failed(self, error: str, now: datetime) -> AiJob:
        self._require(self.status is JobStatus.RUNNING, "fail")
        return replace(self, status=JobStatus.FAILED, error=error, updated_at=now)

    def cancelled(self, now: datetime) -> AiJob:
        self._require(not self.is_terminal, "cancel")
        return replace(self, status=JobStatus.CANCELLED, updated_at=now)

    def with_cancel_requested(self, now: datetime) -> AiJob:
        return replace(self, cancel_requested=True, updated_at=now)

    def retried(self, now: datetime) -> AiJob:
        self._require(self.can_retry, "retry")
        # Resume: keep completed_units as the restart point, spend another attempt.
        return replace(
            self,
            status=JobStatus.QUEUED,
            attempt=self.attempt + 1,
            error=None,
            cancel_requested=False,
            updated_at=now,
        )


def new_generation_job(
    *,
    owner_id: str,
    source_id: str,
    draft_deck_id: str,
    thoroughness: Thoroughness,
    now: datetime,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> AiJob:
    return AiJob(
        id=str(uuid.uuid4()),
        owner_id=owner_id,
        type=JobType.CARD_GENERATION,
        status=JobStatus.QUEUED,
        source_id=source_id,
        draft_deck_id=draft_deck_id,
        thoroughness=thoroughness,
        total_units=0,
        completed_units=0,
        attempt=1,
        max_attempts=max_attempts,
        cancel_requested=False,
        error=None,
        created_at=now,
        updated_at=now,
    )
