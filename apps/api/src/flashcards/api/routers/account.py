# SPDX-License-Identifier: AGPL-3.0-only
"""Account routes: data export, deletion, AI quota and appearance
(F-IMP-5, NF-PRIV-1, F-AI-11)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from flashcards.api.ai_schemas import MyAiQuotaOut
from flashcards.api.container import Container
from flashcards.api.deps import (
    CurrentUser,
    clear_session_cookie,
    current_user,
    get_container,
    get_settings,
)
from flashcards.api.schemas import AccountExportOut, AppearanceOut, AppearanceUpdateRequest
from flashcards.config import Settings
from flashcards.domain.audit import AuditAction
from flashcards.domain.sources import page_cap

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/export", response_model=AccountExportOut)
async def export_account(
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> AccountExportOut:
    export = await container.account.export(user=caller.user)
    return AccountExportOut.from_export(export)


@router.patch("/appearance", response_model=AppearanceOut)
async def update_appearance(
    body: AppearanceUpdateRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> AppearanceOut:
    """Change the caller's light or dark choice and accent palette; it follows them
    to every device."""
    appearance = await container.appearance.update(
        user_id=caller.user.id, theme=body.theme, accent=body.accent
    )
    return AppearanceOut.from_appearance(appearance)


@router.get("/ai-quota", response_model=MyAiQuotaOut)
async def my_ai_quota(
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
    settings: Settings = Depends(get_settings),
) -> MyAiQuotaOut:
    """The caller's monthly AI page limit, how much is used, and the per-document cap.

    An admin has no per-document cap and is not held to the monthly limit (ADR 0022).
    """
    quota = await container.quotas.user_quota(caller.user.id)
    admin = caller.user.is_active_admin
    # A member whose limit in force is unlimited (None) is not metered either.
    unlimited = admin or quota.monthly_pages is None
    return MyAiQuotaOut(
        period=quota.period,
        monthly_pages=None if admin else quota.monthly_pages,
        used_pages=quota.used_pages,
        max_document_pages=page_cap(settings.max_upload_pages, admin=admin),
        quota_applies=not unlimited,
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
    settings: Settings = Depends(get_settings),
) -> Response:
    revoked = await container.account.delete(user=caller.user)
    await container.audit.invites_revoked(revoked, actor_id=caller.user.id)
    await container.audit.record(
        AuditAction.ACCOUNT_DELETED, actor_id=caller.user.id, target_id=caller.user.id
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response, settings)
    # Drop the browser's HTTP cache of this origin, so no cached response of the
    # deleted account stays behind (ADR 0019). Not "storage": that would also wipe
    # other accounts' unsynced offline answers on a shared device and unregister
    # the app's service worker; the web client removes this account's own stored
    # data itself.
    response.headers["Clear-Site-Data"] = '"cache"'
    return response
