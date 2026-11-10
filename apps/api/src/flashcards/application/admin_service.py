# SPDX-License-Identifier: AGPL-3.0-only
"""Admin use cases, each recorded in the audit log (F-AUTH-4, F-ADMIN-1, F-ADMIN-2,
F-ADMIN-5).

Invite management and user management (suspend, unsuspend, role, delete). Every
change leaves an audit event. An admin cannot suspend, demote or delete their own
account, and an admin who was demoted or suspended while a request was in flight
cannot act (AdminRequired). Changes take the admin rows' locks first, so
concurrent ones apply in turn; with both rules, the acting admin always remains,
so there is always at least one active admin. An admin who is suspended,
demoted or deleted loses the unredeemed invites they sent. There is no
impersonation.

Admins can also create accounts directly and set a user's password, for
instances without outgoing mail (a home server). A created account's email
counts as verified, since the admin vouches for it; setting a password signs
the user out everywhere, as a password reset does.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from flashcards.application.account_service import AccountService
from flashcards.application.audit_service import AuditService
from flashcards.application.invite_service import (
    DEFAULT_TTL,
    CreatedInvite,
    InviteService,
    invite_email,
)
from flashcards.application.login_attempts import second_factor_key
from flashcards.application.sign_in_service import throttle_key
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import Role
from flashcards.domain.mail import OutgoingEmail
from flashcards.domain.ports import (
    Clock,
    LoginThrottleRepository,
    PasswordHasher,
    SessionRepository,
    UserRepository,
    UserTokenRepository,
)
from flashcards.domain.user_tokens import TokenPurpose
from flashcards.domain.users import (
    AdminRequired,
    AdminSelfAction,
    EmailAlreadyRegistered,
    User,
    UserNotFound,
    UserStatus,
    validate_email,
    validate_password,
)


class AdminService:
    def __init__(
        self,
        invites: InviteService,
        audit: AuditService,
        users: UserRepository,
        sessions: SessionRepository,
        account: AccountService,
        throttle: LoginThrottleRepository,
        tokens: UserTokenRepository,
        clock: Clock,
        *,
        base_url: str,
        hasher: PasswordHasher,
    ) -> None:
        self._invites = invites
        self._audit = audit
        self._users = users
        self._sessions = sessions
        self._account = account
        self._throttle = throttle
        self._tokens = tokens
        self._clock = clock
        self._base_url = base_url
        self._hasher = hasher

    async def create_invite(
        self,
        *,
        admin_id: str,
        role: Role = Role.MEMBER,
        email: str | None = None,
        ttl: timedelta = DEFAULT_TTL,
    ) -> CreatedInvite:
        # The address may receive the invite email, so it must be a plain, valid one
        # (raises InvalidEmail): no extra recipients or headers can ride along.
        address = validate_email(email) if email else None
        created = await self._invites.create(
            created_by=admin_id, role=role, email=address, ttl=ttl
        )
        await self._audit.record(
            AuditAction.INVITE_CREATED,
            actor_id=admin_id,
            target_id=created.invite.id,
            detail={"role": role.value},
        )
        return created

    def invite_email(self, created: CreatedInvite) -> OutgoingEmail | None:
        """The email for a new or resent invite; None for a link-only invite."""
        return invite_email(created, base_url=self._base_url)

    async def resend_invite(self, *, admin_id: str, invite_id: str) -> CreatedInvite | None:
        """Rotate an active emailed invite's token so its email can be sent again.

        Returns None when the invite is missing or no longer active; raises
        InviteHasNoEmail for a link-only invite.
        """
        rotated = await self._invites.rotate(invite_id)
        if rotated is not None:
            await self._audit.record(
                AuditAction.INVITE_RESENT, actor_id=admin_id, target_id=invite_id
            )
        return rotated

    async def revoke_invite(self, *, admin_id: str, invite_id: str) -> bool:
        revoked = await self._invites.revoke(invite_id)
        if revoked:
            await self._audit.record(
                AuditAction.INVITE_REVOKED, actor_id=admin_id, target_id=invite_id
            )
        return revoked

    async def create_user(
        self, *, admin_id: str, email: str, password: str, role: Role = Role.MEMBER
    ) -> User:
        """Create an account without an invite or any email (raises InvalidEmail,
        WeakPassword or EmailAlreadyRegistered). Its email counts as verified."""
        normalized = validate_email(email)
        validate_password(password)
        await self._lock_admins(admin_id)
        if await self._users.get_by_email(normalized) is not None:
            raise EmailAlreadyRegistered(normalized)
        now = self._clock.now()
        user = User(
            id=str(uuid.uuid4()),
            email=normalized,
            password_hash=await self._hasher.hash(password),
            role=role,
            created_at=now,
            email_verified_at=now,
        )
        await self._users.add(user)  # a concurrent duplicate raises EmailAlreadyRegistered
        await self._audit.record(
            AuditAction.USER_REGISTERED,
            actor_id=admin_id,
            target_id=user.id,
            detail={"via": "admin", "role": role.value},
        )
        return user

    async def set_password(self, *, admin_id: str, user_id: str, password: str) -> User:
        """Set another user's password (raises WeakPassword).

        The user is signed out everywhere, emailed reset and verification links
        stop working, and any sign-in lockout is cleared, as after a reset.
        """
        validate_password(password)
        await self._lock_admins(admin_id)
        user = await self._target(admin_id, user_id)
        changed = user.with_password_hash(await self._hasher.hash(password))
        await self._users.update(changed)
        now = self._clock.now()
        await self._sessions.revoke_all_for_user(user_id, now)
        await self._tokens.invalidate_for_user(user_id, TokenPurpose.PASSWORD_RESET, now)
        await self._throttle.delete(throttle_key(user.email))
        await self._throttle.delete(second_factor_key(user.id))
        await self._audit.record(
            AuditAction.USER_PASSWORD_SET, actor_id=admin_id, target_id=user_id
        )
        return changed

    async def list_users(self) -> list[User]:
        return await self._users.list_all()

    async def suspend_user(self, *, admin_id: str, user_id: str) -> User:
        await self._lock_admins(admin_id)
        user = await self._target(admin_id, user_id)
        if user.status() is UserStatus.SUSPENDED:
            return user
        now = self._clock.now()
        suspended = user.with_suspended(now)
        await self._users.update(suspended)
        await self._sessions.revoke_all_for_user(user_id, now)  # signed out everywhere
        # Links already emailed must not work if the account is unsuspended later.
        for purpose in TokenPurpose:
            await self._tokens.invalidate_for_user(user_id, purpose, now)
        await self._audit.record(AuditAction.USER_SUSPENDED, actor_id=admin_id, target_id=user_id)
        await self._revoke_invites(admin_id, user)
        return suspended

    async def unsuspend_user(self, *, admin_id: str, user_id: str) -> User:
        await self._lock_admins(admin_id)
        user = await self._target(admin_id, user_id)
        if user.status() is not UserStatus.SUSPENDED:
            return user
        restored = user.with_unsuspended()
        await self._users.update(restored)
        await self._audit.record(
            AuditAction.USER_UNSUSPENDED, actor_id=admin_id, target_id=user_id
        )
        return restored

    async def set_role(self, *, admin_id: str, user_id: str, role: Role) -> User:
        await self._lock_admins(admin_id)
        user = await self._target(admin_id, user_id)
        if user.role is role:
            return user
        changed = user.with_role(role)
        await self._users.update(changed)
        await self._audit.record(
            AuditAction.USER_ROLE_CHANGED,
            actor_id=admin_id,
            target_id=user_id,
            detail={"from": user.role.value, "to": role.value},
        )
        await self._revoke_invites(admin_id, user)
        return changed

    async def unlock_user(self, *, admin_id: str, user_id: str) -> User:
        """Clear a sign-in lockout, e.g. when someone keeps an account locked."""
        user = await self._users.get(user_id)
        if user is None:
            raise UserNotFound(user_id)
        await self._throttle.delete(throttle_key(user.email))
        await self._throttle.delete(second_factor_key(user.id))
        await self._audit.record(AuditAction.USER_UNLOCKED, actor_id=admin_id, target_id=user_id)
        return user

    async def delete_user(self, *, admin_id: str, user_id: str) -> None:
        await self._lock_admins(admin_id)
        user = await self._target(admin_id, user_id)
        revoked = await self._account.delete(user=user)  # refuses the last active admin
        await self._audit.record(AuditAction.USER_DELETED, actor_id=admin_id, target_id=user_id)
        await self._audit.invites_revoked(revoked, actor_id=admin_id)

    async def _lock_admins(self, admin_id: str) -> None:
        # Admin rows first, then the target's: one lock order for every change, so
        # concurrent changes wait for each other instead of deadlocking, and each
        # sees who is still an admin once the other has committed. The acting admin
        # must still be one, and never targets themselves, so they always remain.
        admins = await self._users.lock_active_admins()
        if all(admin.id != admin_id for admin in admins):
            raise AdminRequired(admin_id)  # demoted or suspended meanwhile

    async def _target(self, admin_id: str, user_id: str) -> User:
        if user_id == admin_id:
            raise AdminSelfAction(user_id)
        user = await self._users.get_for_update(user_id)
        if user is None:
            raise UserNotFound(user_id)
        return user

    async def _revoke_invites(self, admin_id: str, user: User) -> None:
        """An admin who loses their rights also loses the invites they sent."""
        if user.role is Role.ADMIN:
            revoked = await self._invites.revoke_created_by(user.id)
            await self._audit.invites_revoked(revoked, actor_id=admin_id)

