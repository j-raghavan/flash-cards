# SPDX-License-Identifier: AGPL-3.0-only
"""Run blocking work in worker threads, bounded per kind of work.

Argon2 and the untrusted-file parsers (PyMuPDF, zip and SQLite) are CPU-bound
and synchronous. Running them on the event loop stalls every other request, so
they run in anyio's worker threads instead. Each kind of work has its own
CapacityLimiter, so a burst of sign-ins or uploads waits for a slot instead of
taking every thread (and, for Argon2, its 64 MiB of memory per hash), and one
kind of work cannot starve the other.
"""

from __future__ import annotations

from collections.abc import Callable

import anyio
import anyio.to_thread

from flashcards.domain.anki import AnkiPackage

# Concurrent Argon2 hashes or verifications. Each uses argon2-cffi's default
# 64 MiB, so this also bounds the memory sign-ins can take at once.
PASSWORD_HASHING_SLOTS = 4
# Concurrent .apkg parses (zip and SQLite).
PARSING_SLOTS = 2
# PyMuPDF does not support concurrent use from several threads, so every PDF
# operation (open, count, extract, render, close) takes this single slot.
PYMUPDF_SLOTS = 1

PASSWORD_HASHING = anyio.CapacityLimiter(PASSWORD_HASHING_SLOTS)
PARSING = anyio.CapacityLimiter(PARSING_SLOTS)
PYMUPDF = anyio.CapacityLimiter(PYMUPDF_SLOTS)


async def run_blocking[T](limiter: anyio.CapacityLimiter, func: Callable[[], T]) -> T:
    """Run func in a worker thread once the limiter grants a slot."""
    return await anyio.to_thread.run_sync(func, limiter=limiter)


class BlockingPackageReader:
    """The async PackageReader port over a synchronous parser (such as ApkgReader)."""

    def __init__(self, parse: Callable[[bytes], AnkiPackage]) -> None:
        self._parse = parse

    async def read(self, data: bytes) -> AnkiPackage:
        return await run_blocking(PARSING, lambda: self._parse(data))
