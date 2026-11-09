# SPDX-License-Identifier: AGPL-3.0-only
"""Blocking work runs in worker threads, bounded by a per-kind limiter."""

from __future__ import annotations

import threading
import time

import anyio

from flashcards.adapters import offload
from flashcards.adapters.offload import BlockingPackageReader, run_blocking
from flashcards.domain.anki import AnkiPackage


async def test_run_blocking_runs_in_a_worker_thread() -> None:
    thread = await run_blocking(anyio.CapacityLimiter(1), threading.get_ident)
    assert thread != threading.get_ident()


async def test_the_limiter_bounds_how_many_run_at_once() -> None:
    limiter = anyio.CapacityLimiter(2)
    lock = threading.Lock()
    running = 0
    peak = 0

    def work() -> None:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.02)
        with lock:
            running -= 1

    async with anyio.create_task_group() as group:
        for _ in range(8):
            group.start_soon(run_blocking, limiter, work)
    assert peak == 2


def test_limits_are_small_and_separate() -> None:
    assert offload.PASSWORD_HASHING is not offload.PARSING
    assert offload.PASSWORD_HASHING.total_tokens == offload.PASSWORD_HASHING_SLOTS
    assert offload.PARSING.total_tokens == offload.PARSING_SLOTS
    assert offload.PYMUPDF is not offload.PARSING
    assert offload.PYMUPDF.total_tokens == offload.PYMUPDF_SLOTS == 1


async def test_blocking_package_reader_parses_off_the_event_loop() -> None:
    seen: list[int] = []
    package = AnkiPackage(note_types={}, decks={}, notes=(), cards=())

    def parse(data: bytes) -> AnkiPackage:
        assert data == b"apkg-bytes"
        seen.append(threading.get_ident())
        return package

    assert await BlockingPackageReader(parse).read(b"apkg-bytes") is package
    assert seen and seen[0] != threading.get_ident()
