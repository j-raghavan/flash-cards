# SPDX-License-Identifier: AGPL-3.0-only
"""Admin routes: invite management (F-ADMIN-1). All require the admin role."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, get_container, require_admin
from flashcards.api.schemas import CreatedInviteOut, InviteCreateRequest, InviteOut

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/invites", status_code=status.HTTP_201_CREATED, response_model=CreatedInviteOut)
async def create_invite(
    body: InviteCreateRequest,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container),
) -> CreatedInviteOut:
    created = await container.invites.create(
        created_by=admin.user.id,
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
    if not await container.invites.revoke(invite_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active invite with that id.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
