# SPDX-License-Identifier: AGPL-3.0-only
"""Optional two-factor sign-in: TOTP, passkeys and recovery codes (F-AUTH-3).

Two-factor sign-in is on for an account once it has a confirmed authenticator
app (TOTP) or at least one passkey. Sign-in is then two steps: the password
creates a short-lived, single-use sign-in challenge held on the server, and a
session is issued only when a second factor answers it. Recovery codes stand in
for a lost device; each works once. ADR 0018 records the choices.
"""

from __future__ import annotations

import enum
import re
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

TOTP_DIGITS = 6
# Codes from one 30-second step either side of now are accepted (clock drift).
TOTP_DRIFT_STEPS = 1
# An authenticator app has this long to be confirmed after its secret is shown.
TOTP_SETUP_TTL = timedelta(minutes=15)
# How long the second step of a sign-in, or a passkey registration, may take.
CHALLENGE_TTL = timedelta(minutes=5)
RECOVERY_CODE_COUNT = 10
# 16 characters from a 32-letter alphabet: 80 random bits per code, so a fast
# hash is enough to store them (they cannot be guessed offline from the hash).
_RECOVERY_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
_RECOVERY_GROUPS = 4
_RECOVERY_GROUP_LENGTH = 4
MAX_PASSKEYS = 10
MAX_PASSKEY_NAME = 60
DEFAULT_PASSKEY_NAME = "Passkey"

_TOTP_CODE = re.compile(rf"\d{{{TOTP_DIGITS}}}")
_SEPARATORS = re.compile(r"[\s-]+")


class SecondFactor(enum.StrEnum):
    """What can answer the second step of a sign-in."""

    TOTP = "totp"
    PASSKEY = "passkey"
    RECOVERY_CODE = "recovery_code"


class ChallengePurpose(enum.StrEnum):
    SIGN_IN = "sign_in"
    PASSKEY_REGISTRATION = "passkey_registration"


class TwoFactorError(Exception):
    """Base class for two-factor failures."""


class InvalidSecondFactor(TwoFactorError):
    """The code or passkey did not answer the challenge. Deliberately unspecific."""


class ChallengeExpired(TwoFactorError):
    """The sign-in (or passkey registration) is unknown, used or past its time."""


class InvalidProof(TwoFactorError):
    """Neither the password nor a current code confirmed a sensitive change."""


class TotpAlreadyEnabled(TwoFactorError):
    pass


class TotpNotPending(TwoFactorError):
    """There is no authenticator app waiting to be confirmed (or it took too long)."""


class TotpUnavailable(TwoFactorError):
    """The instance has no key to encrypt authenticator secrets with."""


class TwoFactorNotEnabled(TwoFactorError):
    pass


class PasskeyNotFound(TwoFactorError):
    pass


class TooManyPasskeys(TwoFactorError):
    pass


class InvalidPasskey(TwoFactorError):
    """A WebAuthn response failed verification (registration or sign-in)."""


class SecretUnreadable(TwoFactorError):
    """A stored secret could not be decrypted with any configured key."""


@dataclass(frozen=True, slots=True)
class PendingSignIn:
    """The first step of a sign-in succeeded; a second factor must finish it."""

    token: str  # sent to the client once; only its hash is stored
    methods: tuple[SecondFactor, ...]


class SecondFactorRequired(Exception):
    """The password was right, but the account needs a second factor."""

    def __init__(self, pending: PendingSignIn) -> None:
        self.pending = pending
        super().__init__("second factor required")


@dataclass(frozen=True, slots=True)
class TotpCredential:
    user_id: str
    secret_ciphertext: str  # encrypted at rest; never returned after setup
    created_at: datetime
    confirmed_at: datetime | None = None
    # The newest time step a code was accepted for. A code is accepted only for a
    # later step, so a code cannot be used twice, nor an older one after it.
    last_used_step: int | None = None

    @property
    def confirmed(self) -> bool:
        return self.confirmed_at is not None

    def setup_expired(self, now: datetime) -> bool:
        return not self.confirmed and now - self.created_at >= TOTP_SETUP_TTL


@dataclass(frozen=True, slots=True)
class RecoveryCode:
    id: str
    user_id: str
    code_hash: str
    created_at: datetime
    used_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Passkey:
    id: str
    user_id: str
    credential_id: bytes
    public_key: bytes  # COSE-encoded
    sign_count: int
    name: str
    created_at: datetime
    transports: tuple[str, ...] = ()
    last_used_at: datetime | None = None

    def with_used(self, sign_count: int, now: datetime) -> Passkey:
        return replace(self, sign_count=sign_count, last_used_at=now)


@dataclass(frozen=True, slots=True)
class Challenge:
    """Server-side state between two requests: a pending sign-in or registration.

    The client holds only the random token; its hash is stored. A WebAuthn
    challenge, once issued for it, is kept here and used once.
    """

    id: str
    user_id: str
    purpose: ChallengePurpose
    token_hash: str
    created_at: datetime
    expires_at: datetime
    webauthn_challenge: bytes | None = None
    used_at: datetime | None = None

    def is_live(self, now: datetime, purpose: ChallengePurpose) -> bool:
        return self.purpose is purpose and self.used_at is None and now < self.expires_at

    def with_webauthn(self, challenge: bytes | None) -> Challenge:
        return replace(self, webauthn_challenge=challenge)


@dataclass(frozen=True, slots=True)
class CeremonyOptions:
    """WebAuthn options for the browser, as JSON, and the challenge they carry."""

    options_json: str
    challenge: bytes


@dataclass(frozen=True, slots=True)
class NewPasskey:
    """What a verified WebAuthn registration yields."""

    credential_id: bytes
    public_key: bytes
    sign_count: int
    transports: tuple[str, ...] = ()


def counter_regressed(stored: int, new: int) -> bool:
    """Whether a passkey's signature counter failed to move forward.

    Authenticators that do not count report 0 every time; that is allowed. Any
    other counter must increase, or the passkey may have been cloned.
    """
    return (stored > 0 or new > 0) and new <= stored


def is_totp_code(code: str) -> bool:
    return _TOTP_CODE.fullmatch(_compact(code)) is not None


def compact_code(code: str) -> str:
    """A code as typed, without spaces or hyphens, lower-cased."""
    return _compact(code)


def new_recovery_code() -> str:
    """A fresh recovery code such as "k3m9-x2p4-qrst-uv7w"."""
    groups = (
        "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_GROUP_LENGTH))
        for _ in range(_RECOVERY_GROUPS)
    )
    return "-".join(groups)


def passkey_name(name: str | None) -> str:
    cleaned = " ".join((name or "").split())[:MAX_PASSKEY_NAME]
    return cleaned or DEFAULT_PASSKEY_NAME


def _compact(code: str) -> str:
    return _SEPARATORS.sub("", code).lower()
