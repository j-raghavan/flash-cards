# SPDX-License-Identifier: AGPL-3.0-only
"""The audit log: recording, ordering, paging and the admin route."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.adapters.memory import InMemoryAuditRepository
from flashcards.application.audit_service import AuditService
from flashcards.domain.audit import AuditAction

PASSWORD = "correct-horse-battery"


class _Clock:
    def __init__(self) -> None:
        self.now_value = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.now_value


async def test_record_and_page_newest_first() -> None:
    clock = _Clock()
    audit = AuditService(InMemoryAuditRepository(), clock)
    first = await audit.record(AuditAction.LOGIN_LOCKED)
    clock.now_value += timedelta(minutes=1)
    second = await audit.record(
        AuditAction.USER_ROLE_CHANGED,
        actor_id="admin",
        target_id="u1",
        detail={"to": "admin", "from": "member"},
    )

    assert [e.id for e in await audit.recent(limit=10)] == [second.id, first.id]
    assert second.detail == (("from", "member"), ("to", "admin"))  # stored sorted
    assert [e.id for e in await audit.recent(limit=10, before=second.created_at)] == [first.id]
    # The page size is clamped to at least one event.
    assert len(await audit.recent(limit=0)) == 1


def test_admin_actions_appear_in_the_audit_log(admin_client: TestClient) -> None:
    invite_id = admin_client.post("/admin/invites", json={"role": "member"}).json()["id"]
    assert admin_client.post(f"/admin/invites/{invite_id}/revoke").status_code == 204
    # Revoking again changes nothing and records nothing.
    assert admin_client.post(f"/admin/invites/{invite_id}/revoke").status_code == 404

    events = admin_client.get("/admin/audit").json()

    actions = [(e["action"], e["target_id"]) for e in events]
    assert actions[:2] == [("invite.revoked", invite_id), ("invite.created", invite_id)]
    assert events[1]["detail"] == {"role": "member"}
    assert all(e["actor_id"] for e in events[:2])


def test_audit_log_pages_and_validates(admin_client: TestClient) -> None:
    admin_client.post("/admin/invites", json={"role": "member"})
    admin_client.post("/admin/invites", json={"role": "member"})
    page = admin_client.get("/admin/audit", params={"limit": 1}).json()
    assert len(page) == 1
    older = admin_client.get("/admin/audit", params={"before": page[0]["created_at"]}).json()
    assert all(e["created_at"] < page[0]["created_at"] for e in older)
    assert admin_client.get("/admin/audit", params={"limit": 0}).status_code == 422
    assert admin_client.get("/admin/audit", params={"limit": 201}).status_code == 422


def test_audit_log_is_admin_only(
    app: FastAPI, client: TestClient, seed_invite: Callable[..., str]
) -> None:
    assert client.get("/admin/audit").status_code == 401
    member = TestClient(app)
    signup = member.post(
        "/auth/signup",
        json={"token": seed_invite(), "email": "m@example.com", "password": PASSWORD},
    )
    assert signup.status_code == 201
    assert member.get("/admin/audit").status_code == 403
