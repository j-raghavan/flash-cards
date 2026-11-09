# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import threading

from flashcards.adapters.passwords import Argon2PasswordHasher

PASSWORD = "correct-horse-battery"


async def test_hash_is_not_the_plaintext() -> None:
    hasher = Argon2PasswordHasher()
    digest = await hasher.hash(PASSWORD)
    assert PASSWORD not in digest
    assert digest.startswith("$argon2id$")


async def test_hash_is_salted_so_two_hashes_differ() -> None:
    hasher = Argon2PasswordHasher()
    assert await hasher.hash(PASSWORD) != await hasher.hash(PASSWORD)


async def test_verify_accepts_the_right_password() -> None:
    hasher = Argon2PasswordHasher()
    assert await hasher.verify(await hasher.hash(PASSWORD), PASSWORD) is True


async def test_verify_rejects_the_wrong_password() -> None:
    hasher = Argon2PasswordHasher()
    assert await hasher.verify(await hasher.hash(PASSWORD), "not-the-password") is False


async def test_verify_rejects_a_garbage_hash() -> None:
    hasher = Argon2PasswordHasher()
    assert await hasher.verify("not-a-real-hash", PASSWORD) is False


class _SpyArgon2:
    """Records the thread each call runs on."""

    def __init__(self) -> None:
        self.threads: list[int] = []

    def hash(self, password: str) -> str:
        self.threads.append(threading.get_ident())
        return f"spy:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        self.threads.append(threading.get_ident())
        return password_hash == f"spy:{password}"


async def test_hashing_runs_off_the_event_loop_thread() -> None:
    hasher = Argon2PasswordHasher()
    spy = _SpyArgon2()
    hasher._ph = spy  # type: ignore[assignment]
    assert await hasher.verify(await hasher.hash(PASSWORD), PASSWORD) is True
    assert len(spy.threads) == 2
    assert threading.get_ident() not in spy.threads


def test_dummy_hash_is_a_real_hash_of_a_random_password() -> None:
    hasher = Argon2PasswordHasher()
    first = hasher.dummy_hash()
    assert first.startswith("$argon2id$")
    assert first != hasher.dummy_hash()
