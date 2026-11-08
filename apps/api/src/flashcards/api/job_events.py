# SPDX-License-Identifier: AGPL-3.0-only
"""Server-Sent Events for generation job progress (F-AI-10, ADR 0012).

`job_events` turns successive readings of a job into SSE frames: a `progress`
snapshot on connect and on every change, a `done` snapshot when the job
finishes, and keep-alive comments in between. It only reads through the loader
it is given, so the route decides how each reading touches the database.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi.sse import format_sse_event

from flashcards.api.schemas import JobOut
from flashcards.domain.jobs import AiJob

POLL_SECONDS = 1.0
KEEPALIVE_SECONDS = 15.0
MAX_STREAM_SECONDS = 600.0
RECONNECT_MS = 3_000
MAX_STREAMS_PER_USER = 4

JobLoader = Callable[[], Awaitable[AiJob | None]]
Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


def snapshot_id(job: AiJob) -> str:
    """Identifies the state an event describes (sent as the SSE id)."""
    return ":".join(
        (
            str(job.attempt),
            job.status.value,
            str(job.completed_units),
            str(job.total_units),
            "1" if job.cancel_requested else "0",
        )
    )


def _frame(job: AiJob, *, retry: int | None = None) -> bytes:
    return format_sse_event(
        event="done" if job.is_terminal else "progress",
        data_str=JobOut.from_job(job).model_dump_json(),
        id=snapshot_id(job),
        retry=retry,
    )


async def job_events(
    load: JobLoader,
    *,
    poll_seconds: float = POLL_SECONDS,
    keepalive_seconds: float = KEEPALIVE_SECONDS,
    max_seconds: float = MAX_STREAM_SECONDS,
    sleep: Sleep = asyncio.sleep,
    clock: Clock = time.monotonic,
) -> AsyncIterator[bytes]:
    """SSE frames for one job until it finishes, disappears or max_seconds pass."""
    started = clock()
    job = await load()
    if job is None:
        return
    last = snapshot_id(job)
    yield _frame(job, retry=RECONNECT_MS)
    quiet = 0.0
    while not job.is_terminal and clock() - started < max_seconds:
        await sleep(poll_seconds)
        current = await load()
        if current is None:
            return  # deleted while being watched
        job = current
        if snapshot_id(job) != last:
            last = snapshot_id(job)
            quiet = 0.0
            yield _frame(job)
        else:
            quiet += poll_seconds
            if quiet >= keepalive_seconds:
                quiet = 0.0
                yield format_sse_event(comment="ping")


class StreamSlots:
    """Caps open event streams per user in this process.

    A slot whose stream never released it (say, cancelled before it started)
    expires after the longest a stream can live, so a user can never be locked
    out for good.
    """

    def __init__(
        self,
        limit: int = MAX_STREAMS_PER_USER,
        lifetime: float = MAX_STREAM_SECONDS + 60,
        clock: Clock = time.monotonic,
    ) -> None:
        self._limit = limit
        self._lifetime = lifetime
        self._clock = clock
        self._opened: dict[str, list[float]] = {}

    def acquire(self, user_id: str) -> float | None:
        """A slot token, or None when the user already has `limit` streams open."""
        now = self._clock()
        live = [t for t in self._opened.get(user_id, []) if now - t < self._lifetime]
        if len(live) >= self._limit:
            self._opened[user_id] = live
            return None
        live.append(now)
        self._opened[user_id] = live
        return now

    def release(self, user_id: str, token: float) -> None:
        slots = self._opened.get(user_id, [])
        if token in slots:
            slots.remove(token)
        if not slots:
            self._opened.pop(user_id, None)
