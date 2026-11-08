# SPDX-License-Identifier: AGPL-3.0-only
"""Account routes: data export and deletion (F-IMP-5, NF-PRIV-1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from flashcards.api.container import Container
from flashcards.api.deps import (
    CurrentUser,
    clear_session_cookie,
    current_user,
    get_container,
    get_settings,
)
from flashcards.api.schemas import AccountExportOut
from flashcards.config import Settings
from flashcards.domain.audit import AuditAction

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/export", response_model=AccountExportOut)
async def export_account(
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> AccountExportOut:
    export = await container.account.export(user=caller.user)
    return AccountExportOut.from_export(export)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
    settings: Settings = Depends(get_settings),
) -> Response:
    await container.account.delete(user=caller.user)
    await container.audit.record(
        AuditAction.ACCOUNT_DELETED, actor_id=caller.user.id, target_id=caller.user.id
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response, settings)
    return response
