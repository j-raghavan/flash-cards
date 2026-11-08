# SPDX-License-Identifier: AGPL-3.0-only
"""Admin use cases, each recorded in the audit log (F-AUTH-4, F-ADMIN-1, F-ADMIN-2,
F-ADMIN-5).

Invite management and user management (suspend, unsuspend, role, delete). Every
change leaves an audit event. An admin cannot suspend, demote or delete their own
account, which also guarantees that at least one active admin always remains.
There is no impersonation.
"""

from __future__ import annotations

from datetime import timedelta

from flashcards.application.account_service import AccountService
from flashcards.application.audit_service import AuditService
from flashcards.application.invite_service import DEFAULT_TTL, CreatedInvite, InviteService
from flashcards.application.sign_in_service import throttle_key
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import Role
from flashcards.domain.ports import (
    Clock,
    LoginThrottleRepository,
    SessionRepository,
    UserRepository,
)
from flashcards.domain.users import AdminSelfAction, User, UserNotFound, UserStatus


class AdminService:
    def __init__(
        self,
        invites: InviteService,
        audit: AuditService,
        users: UserRepository,
        sessions: SessionRepository,
        account: AccountService,
        throttle: LoginThrottleRepository,
        clock: Clock,
    ) -> None:
        self._invites = invites
        self._audit = audit
        self._users = users
        self._sessions = sessions
        self._account = account
        self._throttle = throttle
        self._clock = clock

    async def create_invite(
        self,
        *,
        admin_id: str,
        role: Role = Role.MEMBER,
        email: str | None = None,
        ttl: timedelta = DEFAULT_TTL,
    ) -> CreatedInvite:
        created = await self._invites.create(created_by=admin_id, role=role, email=email, ttl=ttl)
        await self._audit.record(
            AuditAction.INVITE_CREATED,
            actor_id=admin_id,
            target_id=created.invite.id,
            detail={"role": role.value},
        )
        return created

    async def revoke_invite(self, *, admin_id: str, invite_id: str) -> bool:
        revoked = await self._invites.revoke(invite_id)
        if revoked:
            await self._audit.record(
                AuditAction.INVITE_REVOKED, actor_id=admin_id, target_id=invite_id
            )
        return revoked

    async def list_users(self) -> list[User]:
        return await self._users.list_all()

    async def suspend_user(self, *, admin_id: str, user_id: str) -> User:
        user = await self._target(admin_id, user_id)
        if user.status() is UserStatus.SUSPENDED:
            return user
        now = self._clock.now()
        suspended = user.with_suspended(now)
        await self._users.update(suspended)
        await self._sessions.revoke_all_for_user(user_id, now)  # signed out everywhere
        await self._audit.record(AuditAction.USER_SUSPENDED, actor_id=admin_id, target_id=user_id)
        return suspended

    async def unsuspend_user(self, *, admin_id: str, user_id: str) -> User:
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
        return changed

    async def unlock_user(self, *, admin_id: str, user_id: str) -> User:
        """Clear a sign-in lockout, e.g. when someone keeps an account locked."""
        user = await self._users.get(user_id)
        if user is None:
            raise UserNotFound(user_id)
        await self._throttle.delete(throttle_key(user.email))
        await self._audit.record(AuditAction.USER_UNLOCKED, actor_id=admin_id, target_id=user_id)
        return user

    async def delete_user(self, *, admin_id: str, user_id: str) -> None:
        user = await self._target(admin_id, user_id)
        await self._account.delete(user=user)
        await self._audit.record(AuditAction.USER_DELETED, actor_id=admin_id, target_id=user_id)

    async def _target(self, admin_id: str, user_id: str) -> User:
        if user_id == admin_id:
            raise AdminSelfAction(user_id)
        user = await self._users.get(user_id)
        if user is None:
            raise UserNotFound(user_id)
        return user
