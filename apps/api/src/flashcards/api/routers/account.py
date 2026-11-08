# SPDX-License-Identifier: AGPL-3.0-only
"""Account routes: data export and deletion (F-IMP-5, NF-PRIV-1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, current_user, get_container
from flashcards.api.schemas import AccountExportOut

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/export", response_model=AccountExportOut)
async def export_account(
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> AccountExportOut:
    export = await container.account.export(user=caller.user)
    return AccountExportOut.from_export(export)
