# SPDX-License-Identifier: AGPL-3.0-only
"""Admin routes: invites, users, quotas, usage and the audit log (F-ADMIN-1..5).

All require the admin role, and every change is recorded in the audit log.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status

from flashcards.api.ai_schemas import (
    InstanceQuotasIn,
    InstanceQuotasOut,
    Period,
    UsageReportOut,
    UserQuotaIn,
    UserQuotaOut,
)
from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, get_container, require_admin
from flashcards.api.mail import send_later
from flashcards.api.schemas import (
    AdminCreateUserRequest,
    AdminSetPasswordRequest,
    AdminUserOut,
    AuditEventOut,
    CreatedInviteOut,
    InviteCreateRequest,
    InviteOut,
    RoleUpdateRequest,
)
from flashcards.application.invite_service import CreatedInvite, InviteHasNoEmail
from flashcards.domain.users import (
    AdminPasswordProtected,
    EmailAlreadyRegistered,
    InvalidEmail,
    WeakPassword,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/invites", status_code=status.HTTP_201_CREATED, response_model=CreatedInviteOut)
async def create_invite(
    body: InviteCreateRequest,
    tasks: BackgroundTasks,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> CreatedInviteOut:
    """Create an invite; one addressed to an email is also sent there."""
    try:
        created = await container.admin.create_invite(
            admin_id=admin.user.id,
            role=body.role,
            email=body.email,
            ttl=timedelta(days=body.ttl_days),
        )
    except InvalidEmail as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    send_later(tasks, container.mailer, container.admin.invite_email(created))
    return _created_out(created)


def _created_out(created: CreatedInvite) -> CreatedInviteOut:
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


@router.post("/invites/{invite_id}/resend", response_model=CreatedInviteOut)
async def resend_invite(
    invite_id: str,
    tasks: BackgroundTasks,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> CreatedInviteOut:
    """Send an emailed invite again with a new token; the previous link stops working.

    Only the token's hash is stored, so the old link cannot be repeated. The new
    token is returned once, like at creation, and the expiry is unchanged.
    """
    try:
        rotated = await container.admin.resend_invite(admin_id=admin.user.id, invite_id=invite_id)
    except InviteHasNoEmail as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This invite has no email address; share its link instead."
        ) from exc
    if rotated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active invite with that id.")
    send_later(tasks, container.mailer, container.admin.invite_email(rotated))
    return _created_out(rotated)


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
    before: str | None = Query(
        default=None, description="The id of the last event of the previous page."
    ),
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
    users = await container.admin.list_users()
    enabled = await container.two_factor.enabled_among([user.id for user in users])
    return [AdminUserOut.from_user(user, two_factor=user.id in enabled) for user in users]


@router.post("/users", status_code=status.HTTP_201_CREATED, response_model=AdminUserOut)
async def create_user(
    body: AdminCreateUserRequest,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> AdminUserOut:
    """Create an account directly: no invite, no email, and the email counts as
    verified. For instances without outgoing mail, such as a home server."""
    try:
        user = await container.admin.create_user(
            admin_id=admin.user.id, email=body.email, password=body.password, role=body.role
        )
    except (InvalidEmail, WeakPassword) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except EmailAlreadyRegistered as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "That email already has an account.") from exc
    return AdminUserOut.from_user(user, two_factor=False)


@router.post("/users/{user_id}/password", response_model=AdminUserOut)
async def set_user_password(
    user_id: str,
    body: AdminSetPasswordRequest,
    tasks: BackgroundTasks,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> AdminUserOut:
    """Set a member's password; they are signed out everywhere and, when mail is
    configured, told by email. Refused (409) for admins: demote them first."""
    try:
        user = await container.admin.set_password(
            admin_id=admin.user.id, user_id=user_id, password=body.password
        )
    except WeakPassword as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except AdminPasswordProtected as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An admin's password cannot be set by another admin. Demote them first.",
        ) from exc
    send_later(tasks, container.mailer, container.admin.password_set_email(user))
    return AdminUserOut.from_user(user)


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


@router.post("/users/{user_id}/unlock", response_model=AdminUserOut)
async def unlock_user(
    user_id: str,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> AdminUserOut:
    user = await container.admin.unlock_user(admin_id=admin.user.id, user_id=user_id)
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


@router.get("/quotas", response_model=InstanceQuotasOut)
async def get_instance_quotas(
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> InstanceQuotasOut:
    return InstanceQuotasOut.from_domain(await container.quotas.instance())


@router.put("/quotas", response_model=InstanceQuotasOut)
async def set_instance_quotas(
    body: InstanceQuotasIn,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> InstanceQuotasOut:
    """Set the default monthly page limit and the monthly spend cap (null: no cap)."""
    quotas = await container.quotas.set_instance(admin_id=admin.user.id, quotas=body.to_domain())
    return InstanceQuotasOut.from_domain(quotas)


@router.get("/users/{user_id}/quota", response_model=UserQuotaOut)
async def get_user_quota(
    user_id: str,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> UserQuotaOut:
    return UserQuotaOut.from_quota(await container.quotas.user_quota(user_id))


@router.put("/users/{user_id}/quota", response_model=UserQuotaOut)
async def set_user_quota(
    user_id: str,
    body: UserQuotaIn,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> UserQuotaOut:
    """Override the user's monthly page limit; null returns them to the default."""
    quota = await container.quotas.set_user_pages(
        admin_id=admin.user.id, user_id=user_id, pages=body.monthly_pages
    )
    return UserQuotaOut.from_quota(quota)


@router.get("/usage", response_model=UsageReportOut)
async def get_usage(
    period: Period | None = Query(default=None, description="A month, e.g. 2026-11."),
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> UsageReportOut:
    """AI calls, tokens and cost for a month, by user and by provider and model."""
    report = await container.quotas.usage_report(period or container.quotas.current_period())
    return UsageReportOut.from_report(report)
