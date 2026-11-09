# SPDX-License-Identifier: AGPL-3.0-only
"""An in-process async job queue and single worker (ADR 0004, Phase 0).

Generation runs inside the API process on the free instance: start() launches a
background task that drains the queue and processes one job at a time, so a slow
LLM call never blocks a request. A bad job is logged and skipped, never fatal.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger("flashcards")

JobProcessor = Callable[[str], Awaitable[None]]


class InProcessJobQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._waiting: set[str] = set()
        self._worker: asyncio.Task[None] | None = None

    async def enqueue(self, job_id: str) -> None:
        # Idempotent: a job already waiting is not queued twice, so the periodic
        # recovery sweep can re-offer every QUEUED job without piling up duplicates.
        if job_id in self._waiting:
            return
        self._waiting.add(job_id)
        await self._queue.put(job_id)

    def start(self, process: JobProcessor) -> None:
        """Launch the worker (idempotent); process is how one job is handled."""
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(process))

    async def _run(self, process: JobProcessor) -> None:
        while True:
            job_id = await self._queue.get()
            self._waiting.discard(job_id)
            try:
                await process(job_id)
            except Exception:  # one bad job must never kill the worker
                logger.exception("generation job %s failed in the worker", job_id)
            finally:
                self._queue.task_done()

    async def join(self) -> None:
        """Wait until every enqueued job has been processed (used in tests)."""
        await self._queue.join()

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
