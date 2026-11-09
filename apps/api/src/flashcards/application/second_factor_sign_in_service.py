# SPDX-License-Identifier: AGPL-3.0-only
"""The second step of a sign-in (F-AUTH-3).

The password step left a pending sign-in on the server: a random token (only
its hash is stored) that lives five minutes and works once. This step answers
it with an authenticator code, a recovery code or a passkey, and only then
issues a session. Every answer is counted against the per-email lockout before
it is checked, so a stolen password does not open unlimited code guessing.
"""

from __future__ import annotations

from flashcards.application.audit_service import AuditService
from flashcards.application.auth_service import AccountSuspended, AuthService, IssuedSession
from flashcards.application.login_attempts import LoginAttempts
from flashcards.application.two_factor_service import TwoFactorService
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import hash_token
from flashcards.domain.ports import Clock, PasskeyCeremonies, TwoFactorRepository, UserRepository
from flashcards.domain.sessions import UNKNOWN_DEVICE
from flashcards.domain.two_factor import (
    Challenge,
    ChallengeExpired,
    ChallengePurpose,
    InvalidPasskey,
    InvalidSecondFactor,
    PasskeyNotFound,
    counter_regressed,
)
from flashcards.domain.users import User, UserStatus


class SecondFactorSignInService:
    def __init__(
        self,
        auth: AuthService,
        two_factor: TwoFactorService,
        repo: TwoFactorRepository,
        users: UserRepository,
        ceremonies: PasskeyCeremonies,
        attempts: LoginAttempts,
        audit: AuditService,
        clock: Clock,
    ) -> None:
        self._auth = auth
        self._two_factor = two_factor
        self._repo = repo
        self._users = users
        self._ceremonies = ceremonies
        self._attempts = attempts
        self._audit = audit
        self._clock = clock

    async def complete_with_code(
        self, *, token: str, code: str, device: str = UNKNOWN_DEVICE
    ) -> IssuedSession:
        """Finish with an authenticator or recovery code.

        Raises InvalidSecondFactor for a wrong code: the caller must still commit,
        so the attempt stays counted.
        """
        challenge, user = await self._pending(token)
        state = await self._attempts.admit(user)
        if not await self._two_factor.verify_code(user, code):
            await self._attempts.failed(user, state)
            raise InvalidSecondFactor()
        return await self._finish(challenge, user, device)

    async def passkey_options(self, token: str) -> str:
        """WebAuthn options for the pending sign-in; the challenge is kept for one answer."""
        challenge, user = await self._pending(token)
        passkeys = await self._repo.list_passkeys(user.id)
        if not passkeys:
            raise PasskeyNotFound(user.id)
        options = self._ceremonies.authentication_options(
            [(p.credential_id, p.transports) for p in passkeys]
        )
        await self._repo.set_webauthn_challenge(challenge.id, options.challenge)
        return options.options_json

    async def complete_with_passkey(
        self, *, token: str, response_json: str, device: str = UNKNOWN_DEVICE
    ) -> IssuedSession:
        """Finish with a passkey assertion. Raises InvalidSecondFactor as above."""
        challenge, user = await self._pending(token)
        state = await self._attempts.admit(user)
        if not await self._passkey_answers(challenge, user, response_json):
            await self._attempts.failed(user, state)
            raise InvalidSecondFactor()
        return await self._finish(challenge, user, device)

    async def _passkey_answers(self, challenge: Challenge, user: User, response_json: str) -> bool:
        expected = challenge.webauthn_challenge
        if expected is None:
            return False
        # A WebAuthn challenge answers once, whether the answer is right or not.
        await self._repo.set_webauthn_challenge(challenge.id, None)
        try:
            passkey = await self._repo.get_passkey_by_credential(
                self._ceremonies.credential_id(response_json)
            )
            if passkey is None or passkey.user_id != user.id:
                return False
            new_count = self._ceremonies.verify_authentication(
                response_json, expected, passkey.public_key
            )
        except InvalidPasskey:
            return False
        if counter_regressed(passkey.sign_count, new_count):
            # The signature is genuine but the counter went back: possibly a clone.
            await self._audit.record(
                AuditAction.PASSKEY_COUNTER_REGRESSED,
                target_id=user.id,
                detail={"passkey_id": passkey.id},
            )
            return False
        return await self._repo.record_passkey_use(
            passkey.id,
            expected_count=passkey.sign_count,
            new_count=new_count,
            now=self._clock.now(),
        )

    async def _pending(self, token: str) -> tuple[Challenge, User]:
        challenge = await self._repo.get_challenge(hash_token(token))
        if challenge is None or not challenge.is_live(self._clock.now(), ChallengePurpose.SIGN_IN):
            raise ChallengeExpired()
        user = await self._users.get(challenge.user_id)
        if user is None:
            raise ChallengeExpired()
        return challenge, user

    async def _finish(self, challenge: Challenge, user: User, device: str) -> IssuedSession:
        # Used atomically, so two right answers racing each other issue one session.
        if not await self._repo.use_challenge(challenge.id, self._clock.now()):
            raise ChallengeExpired()
        if user.status() is UserStatus.SUSPENDED:  # suspended since the password step
            raise AccountSuspended()
        await self._attempts.succeeded(user)
        return await self._auth.start_session(user, device=device)
