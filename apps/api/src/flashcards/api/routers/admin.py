# SPDX-License-Identifier: AGPL-3.0-only
"""Admin routes: invite management and the audit log (F-ADMIN-1, F-ADMIN-5).

All require the admin role, and every change is recorded in the audit log.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, get_container, require_admin
from flashcards.api.schemas import (
    AuditEventOut,
    CreatedInviteOut,
    InviteCreateRequest,
    InviteOut,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/invites", status_code=status.HTTP_201_CREATED, response_model=CreatedInviteOut)
async def create_invite(
    body: InviteCreateRequest,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container),
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
    container: Container = Depends(get_container),
) -> list[InviteOut]:
    now = container.clock.now()
    return [InviteOut.from_invite(inv, now) for inv in await container.invite_repo.list_all()]


@router.post("/invites/{invite_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    invite_id: str,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container),
) -> Response:
    if not await container.admin.revoke_invite(admin_id=admin.user.id, invite_id=invite_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active invite with that id.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/audit", response_model=list[AuditEventOut])
async def list_audit_events(
    limit: int = Query(default=50, gt=0, le=200),
    before: datetime | None = Query(default=None),
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container),
) -> list[AuditEventOut]:
    events = await container.audit.recent(limit=limit, before=before)
    return [AuditEventOut.from_event(event) for event in events]
