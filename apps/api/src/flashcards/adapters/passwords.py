# SPDX-License-Identifier: AGPL-3.0-only
"""Production password hasher using Argon2id (F-AUTH-2)."""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher as _Argon2PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

from flashcards.adapters.offload import PASSWORD_HASHING, run_blocking

_DUMMY_PASSWORD_BYTES = 32


class Argon2PasswordHasher:
    """Argon2id hasher with argon2-cffi's defaults.

    verify() returns a bool and swallows the library's mismatch/invalid-hash
    exceptions, so callers at the login boundary branch on true/false and cannot
    accidentally leak which of "no such user" or "wrong password" occurred.

    Both operations run in a worker thread under the PASSWORD_HASHING limiter, so
    a burst of sign-ins never blocks the event loop and its memory use is bounded.
    """

    def __init__(self) -> None:
        self._ph = _Argon2PasswordHasher()

    async def hash(self, password: str) -> str:
        return await run_blocking(PASSWORD_HASHING, lambda: self._ph.hash(password))

    async def verify(self, password_hash: str, password: str) -> bool:
        return await run_blocking(PASSWORD_HASHING, lambda: self._verify(password_hash, password))

    def _verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._ph.verify(password_hash, password)
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            return False

    def dummy_hash(self) -> str:
        """A hash of a random password, computed synchronously: for startup only.

        AuthService verifies against it when no user matches, so an unknown email
        costs the same Argon2 work as a wrong password.
        """
        return self._ph.hash(secrets.token_urlsafe(_DUMMY_PASSWORD_BYTES))
