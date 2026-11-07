# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from flashcards.adapters.passwords import Argon2PasswordHasher

PASSWORD = "correct-horse-battery"


def test_hash_is_not_the_plaintext() -> None:
    hasher = Argon2PasswordHasher()
    digest = hasher.hash(PASSWORD)
    assert PASSWORD not in digest
    assert digest.startswith("$argon2id$")


def test_hash_is_salted_so_two_hashes_differ() -> None:
    hasher = Argon2PasswordHasher()
    assert hasher.hash(PASSWORD) != hasher.hash(PASSWORD)


def test_verify_accepts_the_right_password() -> None:
    hasher = Argon2PasswordHasher()
    assert hasher.verify(hasher.hash(PASSWORD), PASSWORD) is True


def test_verify_rejects_the_wrong_password() -> None:
    hasher = Argon2PasswordHasher()
    assert hasher.verify(hasher.hash(PASSWORD), "not-the-password") is False


def test_verify_rejects_a_garbage_hash() -> None:
    hasher = Argon2PasswordHasher()
    assert hasher.verify("not-a-real-hash", PASSWORD) is False
