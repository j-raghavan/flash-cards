# SPDX-License-Identifier: AGPL-3.0-only
"""Request and response bodies for two-factor sign-in (F-AUTH-3)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from flashcards.application.two_factor_service import Proof, TwoFactorStatus
from flashcards.domain.two_factor import Passkey, PendingSignIn, SecondFactor

# A WebAuthn response is a few kilobytes; anything far larger is not one.
MAX_CREDENTIAL_JSON = 64 * 1024
_TOKEN = Field(min_length=1, max_length=200)
_CODE = Field(min_length=1, max_length=64)


def _credential_json(value: dict[str, object]) -> dict[str, object]:
    if len(json.dumps(value)) > MAX_CREDENTIAL_JSON:
        raise ValueError("credential is too large")
    return value


class SecondFactorRequiredOut(BaseModel):
    """POST /auth/login answers this (status 202) when a second factor is needed."""

    second_factor_required: Literal[True] = True
    token: str
    methods: list[SecondFactor]

    @classmethod
    def from_pending(cls, pending: PendingSignIn) -> SecondFactorRequiredOut:
        return cls(token=pending.token, methods=list(pending.methods))


class SecondFactorCodeIn(BaseModel):
    token: str = _TOKEN
    code: str = _CODE


class SignInTokenIn(BaseModel):
    token: str = _TOKEN


class PasskeySignInIn(BaseModel):
    token: str = _TOKEN
    credential: dict[str, object]

    _check_credential = field_validator("credential")(_credential_json)


class PasskeyOptionsOut(BaseModel):
    """PublicKeyCredential options for the browser (WebAuthn JSON encoding)."""

    options: dict[str, object]

    @classmethod
    def from_json(cls, options_json: str) -> PasskeyOptionsOut:
        return cls(options=json.loads(options_json))


class ProofIn(BaseModel):
    """The password, or a current authenticator code, confirming a change."""

    password: str | None = Field(default=None, max_length=1_000)
    code: str | None = Field(default=None, max_length=64)

    def to_proof(self) -> Proof:
        return Proof(password=self.password, code=self.code)


class CodeIn(BaseModel):
    code: str = _CODE


class TotpSetupOut(BaseModel):
    secret: str
    uri: str


class RecoveryCodesOut(BaseModel):
    """Recovery codes, shown once; None when none were issued by this change."""

    recovery_codes: list[str] | None


class PasskeyOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    last_used_at: datetime | None

    @classmethod
    def from_passkey(cls, passkey: Passkey) -> PasskeyOut:
        return cls(
            id=passkey.id,
            name=passkey.name,
            created_at=passkey.created_at,
            last_used_at=passkey.last_used_at,
        )


class PasskeyRegistrationOut(BaseModel):
    token: str
    options: dict[str, object]


class PasskeyRegisterIn(BaseModel):
    token: str = _TOKEN
    credential: dict[str, object]
    name: str | None = Field(default=None, max_length=200)

    _check_credential = field_validator("credential")(_credential_json)


class PasskeyAddedOut(BaseModel):
    passkey: PasskeyOut
    recovery_codes: list[str] | None


class TwoFactorStatusOut(BaseModel):
    enabled: bool
    totp: bool
    # False when the instance has no key to encrypt authenticator secrets with.
    totp_available: bool
    passkeys: list[PasskeyOut]
    recovery_codes_left: int

    @classmethod
    def from_status(cls, status: TwoFactorStatus) -> TwoFactorStatusOut:
        return cls(
            enabled=status.enabled,
            totp=status.totp,
            totp_available=status.totp_available,
            passkeys=[PasskeyOut.from_passkey(p) for p in status.passkeys],
            recovery_codes_left=status.recovery_codes_left,
        )
