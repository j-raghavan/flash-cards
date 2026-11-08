# SPDX-License-Identifier: AGPL-3.0-only
"""Admin routes: invites, users and the audit log (F-ADMIN-1, F-ADMIN-2, F-ADMIN-5).

All require the admin role, and every change is recorded in the audit log.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, get_container, require_admin
from flashcards.api.schemas import (
    AdminUserOut,
    AuditEventOut,
    CreatedInviteOut,
    InviteCreateRequest,
    InviteOut,
    RoleUpdateRequest,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/invites", status_code=status.HTTP_201_CREATED, response_model=CreatedInviteOut)
async def create_invite(
    body: InviteCreateRequest,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> CreatedInviteOut:
    created = await container.admin.create_invite(
        admin_id=admin.user.id,
        role=body.role,
        email=body.email,
        ttl=timedelta(days=body.ttl_days),
    )
    return CreatedInviteOut(
        id=created.invite.id,
        token=created.raw_token,
        role=created.invite.role,
        email=created.invite.email,
        expires_at=created.invite.expires_at,
    )


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> list[InviteOut]:
    now = container.clock.now()
    return [InviteOut.from_invite(inv, now) for inv in await container.invite_repo.list_all()]


@router.post("/invites/{invite_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    invite_id: str,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    if not await container.admin.revoke_invite(admin_id=admin.user.id, invite_id=invite_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active invite with that id.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/audit", response_model=list[AuditEventOut])
async def list_audit_events(
    limit: int = Query(default=50, gt=0, le=200),
    before: datetime | None = Query(default=None),
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> list[AuditEventOut]:
    events = await container.audit.recent(limit=limit, before=before)
    return [AuditEventOut.from_event(event) for event in events]


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> list[AdminUserOut]:
    return [AdminUserOut.from_user(user) for user in await container.admin.list_users()]


@router.post("/users/{user_id}/suspend", response_model=AdminUserOut)
async def suspend_user(
    user_id: str,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> AdminUserOut:
    user = await container.admin.suspend_user(admin_id=admin.user.id, user_id=user_id)
    return AdminUserOut.from_user(user)


@router.post("/users/{user_id}/unsuspend", response_model=AdminUserOut)
async def unsuspend_user(
    user_id: str,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> AdminUserOut:
    user = await container.admin.unsuspend_user(admin_id=admin.user.id, user_id=user_id)
    return AdminUserOut.from_user(user)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def set_user_role(
    user_id: str,
    body: RoleUpdateRequest,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> AdminUserOut:
    user = await container.admin.set_role(
        admin_id=admin.user.id, user_id=user_id, role=body.role
    )
    return AdminUserOut.from_user(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    await container.admin.delete_user(admin_id=admin.user.id, user_id=user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
