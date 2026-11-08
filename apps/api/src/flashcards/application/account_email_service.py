# SPDX-License-Identifier: AGPL-3.0-only
"""Email verification and password reset (F-AUTH-2).

Each flow issues a single-use, expiring token, stored only as a hash, and returns
the email to send; the caller sends it after responding, so a request takes the
same time whether or not the account exists. A password reset never reveals
whether an email is registered, revokes every session, clears the sign-in
lockout, and is audited.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from urllib.parse import quote

from flashcards.application.audit_service import AuditService
from flashcards.application.sign_in_service import throttle_key
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import hash_token, normalize_email
from flashcards.domain.mail import OutgoingEmail
from flashcards.domain.ports import (
    Clock,
    LoginThrottleRepository,
    PasswordHasher,
    SessionRepository,
    UserRepository,
    UserTokenRepository,
)
from flashcards.domain.user_tokens import (
    PASSWORD_RESET_TTL,
    RESET_COOLDOWN,
    VERIFICATION_COOLDOWN,
    VERIFICATION_TTL,
    InvalidUserToken,
    TokenPurpose,
    UserToken,
)
from flashcards.domain.users import User, UserStatus, validate_password

TOKEN_BYTES = 32


class AccountEmailService:
    def __init__(
        self,
        users: UserRepository,
        tokens: UserTokenRepository,
        sessions: SessionRepository,
        throttle: LoginThrottleRepository,
        hasher: PasswordHasher,
        audit: AuditService,
        clock: Clock,
        *,
        base_url: str,
    ) -> None:
        self._users = users
        self._tokens = tokens
        self._sessions = sessions
        self._throttle = throttle
        self._hasher = hasher
        self._audit = audit
        self._clock = clock
        self._base_url = base_url.rstrip("/")

    async def verification_email(self, user: User) -> OutgoingEmail | None:
        """A fresh verification email, or None when verified or within the cooldown."""
        if user.is_verified or await self._cooling_down(
            user, TokenPurpose.EMAIL_VERIFICATION, VERIFICATION_COOLDOWN
        ):
            return None
        raw = await self._issue(user, TokenPurpose.EMAIL_VERIFICATION, VERIFICATION_TTL)
        link = self._link("verify-email", raw)
        return OutgoingEmail(
            to=user.email,
            subject="Confirm your email address",
            body=(
                "Confirm your email address for Flash-Cards by opening this link:\n\n"
                f"{link}\n\nThe link expires in 48 hours. If you did not create an "
                "account, you can ignore this message."
            ),
        )

    async def verify_email(self, raw_token: str) -> User:
        token = await self._consume(raw_token, TokenPurpose.EMAIL_VERIFICATION)
        user = await self._live_user(token.user_id)
        if not user.is_verified:
            user = user.with_verified(self._clock.now())
            await self._users.update(user)
            await self._audit.record(
                AuditAction.EMAIL_VERIFIED, actor_id=user.id, target_id=user.id
            )
        return user

    async def password_reset_email(self, email: str) -> OutgoingEmail | None:
        """A reset email for the account, or None when there is nothing to send.

        Nothing is sent for an unknown or suspended email, or within the cooldown
        of the previous reset email; the caller answers the same way regardless.
        """
        user = await self._users.get_by_email(normalize_email(email))
        if user is None or user.status() is UserStatus.SUSPENDED:
            return None
        if await self._cooling_down(user, TokenPurpose.PASSWORD_RESET, RESET_COOLDOWN):
            return None
        raw = await self._issue(user, TokenPurpose.PASSWORD_RESET, PASSWORD_RESET_TTL)
        await self._audit.record(AuditAction.PASSWORD_RESET_REQUESTED, target_id=user.id)
        link = self._link("reset-password", raw)
        return OutgoingEmail(
            to=user.email,
            subject="Reset your password",
            body=(
                "Someone asked to reset the password for your Flash-Cards account. "
                f"To choose a new password, open this link:\n\n{link}\n\nThe link "
                "expires in 1 hour. If this was not you, ignore this message; your "
                "password has not changed."
            ),
        )

    async def reset_password(self, raw_token: str, new_password: str) -> None:
        validate_password(new_password)  # before spending the token
        token = await self._consume(raw_token, TokenPurpose.PASSWORD_RESET)
        user = await self._live_user(token.user_id)
        now = self._clock.now()
        await self._users.update(user.with_password_hash(self._hasher.hash(new_password)))
        # Whoever held the old password, or a session, loses access now.
        await self._sessions.revoke_all_for_user(user.id, now)
        await self._throttle.delete(throttle_key(user.email))
        await self._audit.record(
            AuditAction.PASSWORD_RESET_COMPLETED, actor_id=user.id, target_id=user.id
        )

    def _link(self, page: str, raw_token: str) -> str:
        # The token rides in the fragment, which browsers never send to a server, so
        # it stays out of access logs and Referer headers; the web client reads it.
        return f"{self._base_url}/{page}#token={quote(raw_token)}"

    async def _cooling_down(self, user: User, purpose: TokenPurpose, cooldown: timedelta) -> bool:
        latest = await self._tokens.latest_for_user(user.id, purpose)
        return latest is not None and self._clock.now() - latest.created_at < cooldown

    async def _issue(self, user: User, purpose: TokenPurpose, ttl: timedelta) -> str:
        now = self._clock.now()
        await self._tokens.invalidate_for_user(user.id, purpose, now)
        raw = secrets.token_urlsafe(TOKEN_BYTES)
        await self._tokens.add(
            UserToken(
                id=str(uuid.uuid4()),
                user_id=user.id,
                purpose=purpose,
                token_hash=hash_token(raw),
                created_at=now,
                expires_at=now + ttl,
            )
        )
        return raw

    async def _consume(self, raw_token: str, purpose: TokenPurpose) -> UserToken:
        now = self._clock.now()
        token = await self._tokens.get_by_token_hash(hash_token(raw_token))
        if token is None or not token.is_usable(purpose, now):
            raise InvalidUserToken()
        if not await self._tokens.try_use(token.id, now):  # a concurrent use won
            raise InvalidUserToken()
        return token

    async def _live_user(self, user_id: str) -> User:
        user = await self._users.get(user_id)
        if user is None or user.status() is UserStatus.SUSPENDED:
            raise InvalidUserToken()
        return user
