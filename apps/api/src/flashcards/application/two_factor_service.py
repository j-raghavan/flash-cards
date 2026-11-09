# SPDX-License-Identifier: AGPL-3.0-only
"""Enrol, use and manage second factors (F-AUTH-3).

Changes that weaken or extend an account's sign-in (starting an authenticator
or passkey enrolment, removing one, new recovery codes) need the password or a
current authenticator code as well as the session, so a stolen session alone
cannot take over the account's second factor. Those checks count against the
sign-in lockout.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from flashcards.application.audit_service import AuditService
from flashcards.application.login_attempts import LoginAttempts
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import hash_token
from flashcards.domain.ports import (
    Clock,
    OneTimePasswords,
    PasskeyCeremonies,
    PasswordHasher,
    SecretBox,
    TwoFactorRepository,
    UserRepository,
)
from flashcards.domain.two_factor import (
    CHALLENGE_TTL,
    MAX_PASSKEYS,
    RECOVERY_CODE_COUNT,
    Challenge,
    ChallengeExpired,
    ChallengePurpose,
    InvalidPasskey,
    InvalidProof,
    InvalidSecondFactor,
    Passkey,
    PasskeyNotFound,
    PendingSignIn,
    RecoveryCode,
    SecondFactor,
    SecretUnreadable,
    TooManyPasskeys,
    TotpAlreadyEnabled,
    TotpCredential,
    TotpNotPending,
    TotpUnavailable,
    TwoFactorNotEnabled,
    compact_code,
    is_totp_code,
    new_recovery_code,
    passkey_name,
)
from flashcards.domain.users import User, UserNotFound

ISSUER = "Flash-Cards"
_TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class Proof:
    """The password or a current authenticator code, confirming a sensitive change."""

    password: str | None = None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class TwoFactorStatus:
    totp: bool
    totp_available: bool
    passkeys: tuple[Passkey, ...]
    recovery_codes_left: int

    @property
    def enabled(self) -> bool:
        return self.totp or bool(self.passkeys)


@dataclass(frozen=True, slots=True)
class TotpSetup:
    secret: str  # shown once, for manual entry; the QR code encodes `uri`
    uri: str


@dataclass(frozen=True, slots=True)
class PasskeyRegistration:
    token: str
    options_json: str


@dataclass(frozen=True, slots=True)
class PasskeyAdded:
    passkey: Passkey
    # New recovery codes when this passkey turned two-factor sign-in on.
    recovery_codes: tuple[str, ...] | None


class TwoFactorService:
    def __init__(
        self,
        repo: TwoFactorRepository,
        users: UserRepository,
        hasher: PasswordHasher,
        otp: OneTimePasswords,
        ceremonies: PasskeyCeremonies,
        attempts: LoginAttempts,
        audit: AuditService,
        clock: Clock,
        *,
        box: SecretBox | None,
        issuer: str = ISSUER,
    ) -> None:
        self._repo = repo
        self._users = users
        self._hasher = hasher
        self._otp = otp
        self._ceremonies = ceremonies
        self._attempts = attempts
        self._audit = audit
        self._clock = clock
        # None when the instance has no encryption key: authenticator apps are
        # then unavailable (passkeys keep working: they store no secret).
        self._box = box
        self._issuer = issuer

    # --- status -------------------------------------------------------------

    async def status(self, user_id: str) -> TwoFactorStatus:
        totp = await self._repo.get_totp(user_id)
        return TwoFactorStatus(
            totp=totp is not None and totp.confirmed,
            totp_available=self._box is not None,
            passkeys=tuple(await self._repo.list_passkeys(user_id)),
            recovery_codes_left=await self._repo.count_recovery_codes(user_id),
        )

    async def enabled_among(self, user_ids: Sequence[str]) -> set[str]:
        return await self._repo.enabled_user_ids(user_ids)

    # --- sign-in ------------------------------------------------------------

    async def start_sign_in(self, user: User) -> PendingSignIn | None:
        """A pending sign-in when the account has a second factor, else None."""
        status = await self.status(user.id)
        methods: list[SecondFactor] = []
        if status.totp:
            methods.append(SecondFactor.TOTP)
        if status.passkeys:
            methods.append(SecondFactor.PASSKEY)
        if not methods:
            return None
        if status.recovery_codes_left:
            methods.append(SecondFactor.RECOVERY_CODE)
        token = await self._new_challenge(user.id, ChallengePurpose.SIGN_IN)
        return PendingSignIn(token=token, methods=tuple(methods))

    async def verify_code(self, user: User, code: str) -> bool:
        """Whether `code` is a current authenticator code or an unused recovery code.

        Either is used up by a successful check.
        """
        if is_totp_code(code):
            return await self._verify_totp(user.id, code)
        used = await self._repo.use_recovery_code(
            user.id, hash_token(compact_code(code)), self._clock.now()
        )
        if used:
            left = await self._repo.count_recovery_codes(user.id)
            await self._audit.record(
                AuditAction.RECOVERY_CODE_USED,
                actor_id=user.id,
                target_id=user.id,
                detail={"remaining": str(left)},
            )
        return used

    # --- proof for sensitive changes ----------------------------------------

    async def check_proof(self, user: User, proof: Proof) -> None:
        """Raise InvalidProof unless the password or a current code is given.

        Counted against the sign-in lockout (LoginLocked once it is reached).
        """
        state = await self._attempts.admit(user)
        confirmed = False
        if proof.code:
            confirmed = await self._verify_totp(user.id, proof.code)
        if not confirmed and proof.password:
            confirmed = await self._hasher.verify(user.password_hash, proof.password)
        if not confirmed:
            await self._attempts.failed(user, state)
            raise InvalidProof()
        await self._attempts.succeeded(user)

    # --- authenticator app (TOTP) -------------------------------------------

    async def begin_totp(self, user: User, proof: Proof) -> TotpSetup:
        box = self._box
        if box is None:
            raise TotpUnavailable()
        existing = await self._repo.get_totp(user.id)
        if existing is not None and existing.confirmed:
            raise TotpAlreadyEnabled()
        await self.check_proof(user, proof)
        secret = self._otp.new_secret()
        await self._repo.save_totp(
            TotpCredential(
                user_id=user.id,
                secret_ciphertext=box.seal(secret),
                created_at=self._clock.now(),
            )
        )
        uri = self._otp.provisioning_uri(secret, account=user.email, issuer=self._issuer)
        return TotpSetup(secret=secret, uri=uri)

    async def confirm_totp(self, user: User, code: str) -> tuple[str, ...] | None:
        """Turn the app on with its first code; new recovery codes if 2FA was off."""
        now = self._clock.now()
        credential = await self._repo.get_totp(user.id)
        if credential is None or credential.confirmed or credential.setup_expired(now):
            raise TotpNotPending()
        secret = self._open(credential)
        if secret is None:
            raise TotpNotPending()
        # Counted, so a stolen session cannot guess the code of a pending setup
        # (and receive the new recovery codes). The caller commits a failure.
        state = await self._attempts.admit(user)
        step = self._otp.matching_step(secret, compact_code(code), now)
        if step is None:
            await self._attempts.failed(user, state)
            raise InvalidSecondFactor()
        await self._attempts.succeeded(user)
        was_enabled = await self._enabled(user.id)
        if not await self._repo.confirm_totp(user.id, step, now):
            raise TotpNotPending()  # confirmed meanwhile by a concurrent request
        await self._audit.record(AuditAction.TOTP_ENABLED, actor_id=user.id, target_id=user.id)
        return None if was_enabled else await self._issue_recovery_codes(user)

    async def disable_totp(self, user: User, proof: Proof) -> None:
        credential = await self._repo.get_totp(user.id)
        if credential is None or not credential.confirmed:
            raise TwoFactorNotEnabled()
        await self.check_proof(user, proof)
        await self._repo.delete_totp(user.id)
        await self._audit.record(AuditAction.TOTP_DISABLED, actor_id=user.id, target_id=user.id)
        await self._forget_codes_if_off(user.id)

    # --- recovery codes -----------------------------------------------------

    async def regenerate_recovery_codes(self, user: User, proof: Proof) -> tuple[str, ...]:
        if not await self._enabled(user.id):
            raise TwoFactorNotEnabled()
        await self.check_proof(user, proof)
        return await self._issue_recovery_codes(user)

    # --- passkeys -----------------------------------------------------------

    async def begin_passkey(self, user: User, proof: Proof) -> PasskeyRegistration:
        passkeys = await self._repo.list_passkeys(user.id)
        if len(passkeys) >= MAX_PASSKEYS:
            raise TooManyPasskeys()
        await self.check_proof(user, proof)
        options = self._ceremonies.registration_options(
            user_handle=user.id.encode(),
            user_name=user.email,
            exclude=[p.credential_id for p in passkeys],
        )
        token = await self._new_challenge(
            user.id, ChallengePurpose.PASSKEY_REGISTRATION, webauthn=options.challenge
        )
        return PasskeyRegistration(token=token, options_json=options.options_json)

    async def finish_passkey(
        self, user: User, *, token: str, response_json: str, name: str | None
    ) -> PasskeyAdded:
        now = self._clock.now()
        challenge = await self._repo.get_challenge(hash_token(token))
        if (
            challenge is None
            or challenge.user_id != user.id
            or not challenge.is_live(now, ChallengePurpose.PASSKEY_REGISTRATION)
            or challenge.webauthn_challenge is None
        ):
            raise ChallengeExpired()
        if not await self._repo.use_challenge(challenge.id, now):
            raise ChallengeExpired()
        verified = self._ceremonies.verify_registration(
            response_json, challenge.webauthn_challenge
        )
        if await self._repo.get_passkey_by_credential(verified.credential_id) is not None:
            raise InvalidPasskey("this passkey is already registered")
        was_enabled = await self._enabled(user.id)
        passkey = Passkey(
            id=str(uuid.uuid4()),
            user_id=user.id,
            credential_id=verified.credential_id,
            public_key=verified.public_key,
            sign_count=verified.sign_count,
            name=passkey_name(name),
            created_at=now,
            transports=verified.transports,
        )
        await self._repo.add_passkey(passkey)
        await self._audit.record(
            AuditAction.PASSKEY_ADDED,
            actor_id=user.id,
            target_id=user.id,
            detail={"passkey_id": passkey.id},
        )
        codes = None if was_enabled else await self._issue_recovery_codes(user)
        return PasskeyAdded(passkey=passkey, recovery_codes=codes)

    async def remove_passkey(self, user: User, passkey_id: str, proof: Proof) -> None:
        if all(p.id != passkey_id for p in await self._repo.list_passkeys(user.id)):
            raise PasskeyNotFound(passkey_id)
        await self.check_proof(user, proof)
        await self._repo.delete_passkey(user.id, passkey_id)
        await self._audit.record(
            AuditAction.PASSKEY_REMOVED,
            actor_id=user.id,
            target_id=user.id,
            detail={"passkey_id": passkey_id},
        )
        await self._forget_codes_if_off(user.id)

    # --- administration -----------------------------------------------------

    async def reset(self, *, admin_id: str, user_id: str) -> User:
        """Remove every second factor of a user who lost theirs (audited)."""
        user = await self._users.get(user_id)
        if user is None:
            raise UserNotFound(user_id)
        await self._repo.delete_for_user(user_id)
        await self._audit.record(AuditAction.TWO_FACTOR_RESET, actor_id=admin_id, target_id=user_id)
        return user

    # --- helpers ------------------------------------------------------------

    async def _verify_totp(self, user_id: str, code: str) -> bool:
        credential = await self._repo.get_totp(user_id)
        if credential is None or not credential.confirmed:
            return False
        secret = self._open(credential)
        if secret is None:
            return False
        step = self._otp.matching_step(secret, compact_code(code), self._clock.now())
        # Recording the step is atomic and only moves forward: a code works once.
        return step is not None and await self._repo.use_totp_step(user_id, step)

    def _open(self, credential: TotpCredential) -> str | None:
        if self._box is None:
            return None
        try:
            return self._box.open(credential.secret_ciphertext)
        except SecretUnreadable:
            return None

    async def _enabled(self, user_id: str) -> bool:
        return user_id in await self._repo.enabled_user_ids([user_id])

    async def _forget_codes_if_off(self, user_id: str) -> None:
        if not await self._enabled(user_id):
            await self._repo.delete_recovery_codes(user_id)

    async def _issue_recovery_codes(self, user: User) -> tuple[str, ...]:
        now = self._clock.now()
        codes = tuple(new_recovery_code() for _ in range(RECOVERY_CODE_COUNT))
        await self._repo.replace_recovery_codes(
            user.id,
            [
                RecoveryCode(
                    id=str(uuid.uuid4()),
                    user_id=user.id,
                    code_hash=hash_token(compact_code(code)),
                    created_at=now,
                )
                for code in codes
            ],
        )
        await self._audit.record(
            AuditAction.RECOVERY_CODES_GENERATED, actor_id=user.id, target_id=user.id
        )
        return codes

    async def _new_challenge(
        self, user_id: str, purpose: ChallengePurpose, *, webauthn: bytes | None = None
    ) -> str:
        now = self._clock.now()
        await self._repo.delete_stale_challenges(user_id, now)
        raw_token = secrets.token_urlsafe(_TOKEN_BYTES)
        await self._repo.add_challenge(
            Challenge(
                id=str(uuid.uuid4()),
                user_id=user_id,
                purpose=purpose,
                token_hash=hash_token(raw_token),
                created_at=now,
                expires_at=now + CHALLENGE_TTL,
                webauthn_challenge=webauthn,
            )
        )
        return raw_token
