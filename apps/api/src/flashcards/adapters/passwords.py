# SPDX-License-Identifier: AGPL-3.0-only
"""Production password hasher using Argon2id (F-AUTH-2)."""

from __future__ import annotations

from argon2 import PasswordHasher as _Argon2PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)


class Argon2PasswordHasher:
    """Argon2id hasher with argon2-cffi's defaults.

    verify() returns a bool and swallows the library's mismatch/invalid-hash
    exceptions, so callers at the login boundary branch on true/false and cannot
    accidentally leak which of "no such user" or "wrong password" occurred.
    """

    def __init__(self) -> None:
        self._ph = _Argon2PasswordHasher()

    def hash(self, password: str) -> str:
        return self._ph.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._ph.verify(password_hash, password)
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            return False
