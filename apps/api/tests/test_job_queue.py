# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from flashcards.adapters.job_queue import InProcessJobQueue


async def test_processes_enqueued_jobs() -> None:
    seen: list[str] = []

    async def process(job_id: str) -> None:
        seen.append(job_id)

    queue = InProcessJobQueue()
    queue.start(process)
    await queue.enqueue("a")
    await queue.enqueue("b")
    await queue.join()
    await queue.stop()

    assert seen == ["a", "b"]


async def test_worker_survives_a_failing_job() -> None:
    seen: list[str] = []

    async def process(job_id: str) -> None:
        if job_id == "bad":
            raise RuntimeError("boom")
        seen.append(job_id)

    queue = InProcessJobQueue()
    queue.start(process)
    await queue.enqueue("bad")
    await queue.enqueue("good")
    await queue.join()
    await queue.stop()

    assert seen == ["good"]  # the bad job was logged and skipped, not fatal


async def test_start_is_idempotent_and_stop_is_safe_before_start() -> None:
    calls: list[str] = []

    async def process(job_id: str) -> None:
        calls.append(job_id)

    queue = InProcessJobQueue()
    await queue.stop()  # never started: a no-op
    queue.start(process)
    queue.start(process)  # second start does nothing
    await queue.enqueue("x")
    await queue.join()
    await queue.stop()

    assert calls == ["x"]


async def test_a_job_already_waiting_is_not_queued_twice() -> None:
    seen: list[str] = []

    async def process(job_id: str) -> None:
        seen.append(job_id)

    queue = InProcessJobQueue()
    await queue.enqueue("a")
    await queue.enqueue("a")  # still waiting: ignored
    queue.start(process)
    await queue.join()
    await queue.enqueue("a")  # taken by the worker already: offered again
    await queue.join()
    await queue.stop()

    assert seen == ["a", "a"]
