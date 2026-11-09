# SPDX-License-Identifier: AGPL-3.0-only
"""Second-factor adapters (F-AUTH-3): Fernet encryption, pyotp and py_webauthn."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Sequence
from datetime import datetime
from urllib.parse import urlsplit

import pyotp
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.parse_authentication_credential_json import (
    parse_authentication_credential_json,
)
from webauthn.helpers.parse_registration_credential_json import (
    parse_registration_credential_json,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from flashcards.domain.two_factor import (
    TOTP_DIGITS,
    TOTP_DRIFT_STEPS,
    CeremonyOptions,
    InvalidPasskey,
    NewPasskey,
    SecretUnreadable,
)

logger = logging.getLogger(__name__)

_DEFAULT_PORTS = {"http": 80, "https": 443}
# A reserved name (RFC 2606): never a real page's host.
_UNUSABLE_RP = ("invalid.invalid", "https://invalid.invalid")
# What a malformed WebAuthn response can raise while it is parsed and checked.
_BAD_RESPONSE = (WebAuthnException, ValueError, KeyError, TypeError)


def parse_keys(raw: str) -> list[str]:
    """FLASHCARDS_TOTP_ENCRYPTION_KEYS: comma-separated Fernet keys, newest first."""
    return [key.strip() for key in raw.split(",") if key.strip()]


class FernetSecretBox:
    """Authenticated encryption (AES-128-CBC with HMAC-SHA256) under rotating keys.

    The first key encrypts; every key decrypts, so a new key can be put in front
    and the old one removed once `flashcards rotate-totp-key` has re-encrypted
    the stored secrets.
    """

    def __init__(self, keys: Sequence[str]) -> None:
        if not keys:
            raise ValueError("at least one key is required")
        # Fernet raises ValueError for a key that is not 32 url-safe base64 bytes,
        # so a mistyped key fails at startup rather than at first use.
        self._fernet = MultiFernet([Fernet(key.encode()) for key in keys])

    @classmethod
    def ephemeral(cls) -> FernetSecretBox:
        """A box with a random key, for in-memory mode where nothing outlives the process."""
        return cls([Fernet.generate_key().decode()])

    def seal(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def open(self, sealed: str) -> str:
        try:
            return self._fernet.decrypt(sealed.encode()).decode()
        except InvalidToken as exc:
            raise SecretUnreadable() from exc

    def reseal(self, sealed: str) -> str:
        try:
            return self._fernet.rotate(sealed.encode()).decode()
        except InvalidToken as exc:
            raise SecretUnreadable() from exc


class PyOtpPasswords:
    """RFC 6238 codes: 6 digits, 30-second steps, SHA-1 (what authenticator apps expect)."""

    def new_secret(self) -> str:
        return pyotp.random_base32()  # 160 bits

    def provisioning_uri(self, secret: str, *, account: str, issuer: str) -> str:
        return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)

    def matching_step(self, secret: str, code: str, now: datetime) -> int | None:
        if len(code) != TOTP_DIGITS or not (code.isascii() and code.isdigit()):
            return None
        totp = pyotp.TOTP(secret)
        current = totp.timecode(now)
        match: int | None = None
        # Every candidate is compared, in constant time, whichever one matches.
        for step in range(current - TOTP_DRIFT_STEPS, current + TOTP_DRIFT_STEPS + 1):
            if hmac.compare_digest(totp.generate_otp(step), code):
                match = step
        return match


def relying_party(public_base_url: str) -> tuple[str, str]:
    """The WebAuthn RP id (the host) and origin of the web client's public URL."""
    parts = urlsplit(public_base_url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in _DEFAULT_PORTS or not host:
        raise ValueError(f"not an http(s) URL: {public_base_url!r}")
    port = parts.port
    origin = f"{parts.scheme}://{host}"
    if port is not None and port != _DEFAULT_PORTS[parts.scheme]:
        origin += f":{port}"
    return host, origin


class WebAuthnCeremonies:
    """Passkeys as a second factor, for the relying party at FLASHCARDS_PUBLIC_BASE_URL.

    Passkeys are not required to be discoverable and user verification is
    preferred, not required: the password is the first factor and the passkey
    proves possession of the device.
    """

    def __init__(self, *, public_base_url: str, rp_name: str = "Flash-Cards") -> None:
        try:
            self._rp_id, self._origin = relying_party(public_base_url)
        except ValueError:
            # A misconfigured URL must not stop the API from starting; no browser
            # page can match this relying party, so passkeys simply do not work.
            logger.warning("FLASHCARDS_PUBLIC_BASE_URL is not a URL: passkeys are unavailable.")
            self._rp_id, self._origin = _UNUSABLE_RP
        self._rp_name = rp_name

    def registration_options(
        self, *, user_handle: bytes, user_name: str, exclude: Sequence[bytes]
    ) -> CeremonyOptions:
        options = generate_registration_options(
            rp_id=self._rp_id,
            rp_name=self._rp_name,
            user_name=user_name,
            user_id=user_handle,
            user_display_name=user_name,
            exclude_credentials=[PublicKeyCredentialDescriptor(id=c) for c in exclude],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.DISCOURAGED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
        )
        return CeremonyOptions(options_json=options_to_json(options), challenge=options.challenge)

    def verify_registration(self, response_json: str, challenge: bytes) -> NewPasskey:
        try:
            credential = parse_registration_credential_json(response_json)
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
            )
        except _BAD_RESPONSE as exc:
            raise InvalidPasskey("the passkey could not be verified") from exc
        transports = credential.response.transports or []
        return NewPasskey(
            credential_id=verified.credential_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            transports=tuple(t.value for t in transports),
        )

    def authentication_options(
        self, allow: Sequence[tuple[bytes, tuple[str, ...]]]
    ) -> CeremonyOptions:
        options = generate_authentication_options(
            rp_id=self._rp_id,
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=credential_id, transports=_transports(names))
                for credential_id, names in allow
            ],
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        return CeremonyOptions(options_json=options_to_json(options), challenge=options.challenge)

    def credential_id(self, response_json: str) -> bytes:
        try:
            return parse_authentication_credential_json(response_json).raw_id
        except _BAD_RESPONSE as exc:
            raise InvalidPasskey("the passkey response is malformed") from exc

    def verify_authentication(
        self, response_json: str, challenge: bytes, public_key: bytes
    ) -> int:
        try:
            verified = verify_authentication_response(
                credential=response_json,
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                credential_public_key=public_key,
                # The caller compares the counter with the stored one after the
                # signature is verified, so a cloned passkey can be told apart from
                # a forged answer (and audited).
                credential_current_sign_count=0,
            )
        except _BAD_RESPONSE as exc:
            raise InvalidPasskey("the passkey could not be verified") from exc
        return verified.new_sign_count


def _transports(names: tuple[str, ...]) -> list[AuthenticatorTransport] | None:
    known = {t.value: t for t in AuthenticatorTransport}
    found = [known[name] for name in names if name in known]
    return found or None
