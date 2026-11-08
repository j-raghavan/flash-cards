# SPDX-License-Identifier: AGPL-3.0-only
"""Admin user management (F-AUTH-4, F-ADMIN-2), each action audited."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.adapters.memory import InMemoryUserRepository
from flashcards.api.container import Container
from flashcards.domain.invites import Role
from flashcards.domain.users import AdminRequired

PASSWORD = "correct-horse-battery"
MEMBER = "member@example.com"


def _member(app: FastAPI, seed_invite: Callable[..., str]) -> tuple[TestClient, str]:
    client = TestClient(app)
    resp = client.post(
        "/auth/signup", json={"token": seed_invite(), "email": MEMBER, "password": PASSWORD}
    )
    assert resp.status_code == 201
    user_id: str = resp.json()["id"]
    return client, user_id


def _actions(admin: TestClient) -> list[tuple[str, str | None]]:
    return [(e["action"], e["target_id"]) for e in admin.get("/admin/audit").json()]


def _admin_id(admin: TestClient) -> str:
    me: str = admin.get("/auth/me").json()["id"]
    return me


def test_admin_lists_users(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> None:
    _client, member_id = _member(app, seed_invite)
    users = {u["id"]: u for u in admin_client.get("/admin/users").json()}
    assert users[member_id]["email"] == MEMBER and users[member_id]["role"] == "member"
    assert users[member_id]["suspended"] is False and users[member_id]["email_verified"] is False
    assert users[_admin_id(admin_client)]["role"] == "admin"


def test_suspend_signs_the_user_out_and_blocks_sign_in(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> None:
    member, member_id = _member(app, seed_invite)

    suspended = admin_client.post(f"/admin/users/{member_id}/suspend")

    assert suspended.status_code == 200 and suspended.json()["suspended"] is True
    assert member.get("/auth/me").status_code == 401
    login = TestClient(app).post("/auth/login", json={"email": MEMBER, "password": PASSWORD})
    assert login.status_code == 403
    # Suspending again changes nothing and records nothing new.
    assert admin_client.post(f"/admin/users/{member_id}/suspend").status_code == 200
    assert _actions(admin_client).count(("user.suspended", member_id)) == 1

    restored = admin_client.post(f"/admin/users/{member_id}/unsuspend")
    assert restored.status_code == 200 and restored.json()["suspended"] is False
    login = TestClient(app).post("/auth/login", json={"email": MEMBER, "password": PASSWORD})
    assert login.status_code == 200
    assert admin_client.post(f"/admin/users/{member_id}/unsuspend").status_code == 200
    assert _actions(admin_client).count(("user.unsuspended", member_id)) == 1


def test_role_change_grants_admin_access(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> None:
    member, member_id = _member(app, seed_invite)
    assert member.get("/admin/users").status_code == 403

    promoted = admin_client.patch(f"/admin/users/{member_id}", json={"role": "admin"})

    assert promoted.status_code == 200 and promoted.json()["role"] == "admin"
    assert member.get("/admin/users").status_code == 200
    event = admin_client.get("/admin/audit").json()[0]
    assert event["action"] == "user.role_changed"
    assert event["detail"] == {"from": "member", "to": "admin"}
    # Setting the same role again is a no-op.
    same = admin_client.patch(f"/admin/users/{member_id}", json={"role": "admin"})
    assert same.status_code == 200
    assert _actions(admin_client).count(("user.role_changed", member_id)) == 1
    bad = admin_client.patch(f"/admin/users/{member_id}", json={"role": "owner"})
    assert bad.status_code == 422


def test_delete_removes_the_account(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> None:
    _member_client, member_id = _member(app, seed_invite)

    assert admin_client.delete(f"/admin/users/{member_id}").status_code == 204

    assert member_id not in {u["id"] for u in admin_client.get("/admin/users").json()}
    login = TestClient(app).post("/auth/login", json={"email": MEMBER, "password": PASSWORD})
    assert login.status_code == 401
    assert ("user.deleted", member_id) in _actions(admin_client)
    assert admin_client.delete(f"/admin/users/{member_id}").status_code == 404


def test_admins_cannot_act_on_themselves(admin_client: TestClient) -> None:
    me = _admin_id(admin_client)
    for response in (
        admin_client.post(f"/admin/users/{me}/suspend"),
        admin_client.post(f"/admin/users/{me}/unsuspend"),
        admin_client.patch(f"/admin/users/{me}", json={"role": "member"}),
        admin_client.delete(f"/admin/users/{me}"),
    ):
        assert response.status_code == 409
        assert response.json()["detail"] == "Admins cannot do this to their own account."
    assert admin_client.get("/auth/me").json()["role"] == "admin"


def test_unknown_users_are_404(admin_client: TestClient) -> None:
    assert admin_client.post("/admin/users/missing/suspend").status_code == 404
    assert admin_client.patch("/admin/users/missing", json={"role": "admin"}).status_code == 404


def test_user_management_is_admin_only(
    app: FastAPI, client: TestClient, seed_invite: Callable[..., str]
) -> None:
    member, member_id = _member(app, seed_invite)
    assert client.get("/admin/users").status_code == 401
    assert member.get("/admin/users").status_code == 403
    assert member.post(f"/admin/users/{member_id}/suspend").status_code == 403


def test_self_service_account_deletion_is_audited(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> None:
    member, member_id = _member(app, seed_invite)
    assert member.delete("/account").status_code == 204
    assert ("account.deleted", member_id) in _actions(admin_client)


def test_admin_can_clear_a_sign_in_lockout(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> None:
    _member_client, member_id = _member(app, seed_invite)
    stranger = TestClient(app)
    for _ in range(5):
        stranger.post("/auth/login", json={"email": MEMBER, "password": "wrong"})
    right = {"email": MEMBER, "password": PASSWORD}
    assert TestClient(app).post("/auth/login", json=right).status_code == 429

    unlocked = admin_client.post(f"/admin/users/{member_id}/unlock")

    assert unlocked.status_code == 200
    assert TestClient(app).post("/auth/login", json=right).status_code == 200
    assert ("user.unlocked", member_id) in _actions(admin_client)
    assert admin_client.post("/admin/users/missing/unlock").status_code == 404


def _promoted_admin(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> tuple[TestClient, str, dict[str, str]]:
    """A member promoted to admin, who then sends an invite."""
    other, other_id = _member(app, seed_invite)
    admin_client.patch(f"/admin/users/{other_id}", json={"role": "admin"})
    invite: dict[str, str] = other.post("/admin/invites", json={}).json()
    return other, other_id, invite


def _redeemable(app: FastAPI, token: str) -> bool:
    body = {"token": token, "email": "newcomer@example.com", "password": PASSWORD}
    return TestClient(app).post("/auth/signup", json=body).status_code == 201


def test_a_demoted_admin_loses_the_invites_they_sent(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> None:
    _other, other_id, invite = _promoted_admin(app, admin_client, seed_invite)
    kept = admin_client.post("/admin/invites", json={}).json()

    admin_client.patch(f"/admin/users/{other_id}", json={"role": "member"})

    assert not _redeemable(app, invite["token"])
    events = admin_client.get("/admin/audit").json()
    event = next(e for e in events if e["target_id"] == invite["id"])
    assert event["action"] == "invite.revoked" and event["detail"] == {"reason": "creator removed"}
    assert _redeemable(app, kept["token"])  # other admins' invites are untouched


def test_a_suspended_or_deleted_admin_loses_the_invites_they_sent(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> None:
    _other, other_id, invite = _promoted_admin(app, admin_client, seed_invite)
    admin_client.post(f"/admin/users/{other_id}/suspend")
    assert not _redeemable(app, invite["token"])

    admin_client.post(f"/admin/users/{other_id}/unsuspend")
    sender = TestClient(app)  # signed in again after the suspension
    assert sender.post("/auth/login", json={"email": MEMBER, "password": PASSWORD}).is_success
    second: dict[str, str] = sender.post("/admin/invites", json={}).json()
    admin_client.delete(f"/admin/users/{other_id}")
    assert not _redeemable(app, second["token"])
    assert ("invite.revoked", second["id"]) in _actions(admin_client)


def test_the_last_active_admin_cannot_delete_their_account(admin_client: TestClient) -> None:
    refused = admin_client.delete("/account")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "At least one active admin must remain."
    assert admin_client.get("/auth/me").json()["role"] == "admin"


def test_an_admin_can_delete_their_account_while_another_remains(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> None:
    other, other_id, invite = _promoted_admin(app, admin_client, seed_invite)
    assert other.delete("/account").status_code == 204
    assert not _redeemable(app, invite["token"])
    assert ("invite.revoked", invite["id"]) in _actions(admin_client)


def test_an_admin_demoted_mid_request_cannot_act(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> None:
    container: Container = app.state.container
    _other, other_id, _invite = _promoted_admin(app, admin_client, seed_invite)
    me = _admin_id(admin_client)
    # Two admins demote each other at the same time: the second applies after the
    # first has committed, when its admin is no longer one, so one admin remains.
    asyncio.run(container.admin.set_role(admin_id=me, user_id=other_id, role=Role.MEMBER))
    with pytest.raises(AdminRequired):
        asyncio.run(container.admin.set_role(admin_id=other_id, user_id=me, role=Role.MEMBER))
    assert admin_client.get("/auth/me").json()["role"] == "admin"


def test_suspension_voids_links_already_emailed(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> None:
    container: Container = app.state.container
    _member_client, member_id = _member(app, seed_invite)
    email = asyncio.run(container.account_email.password_reset_email(MEMBER))
    assert email is not None
    token = email.body.split("#token=")[1].split()[0]

    admin_client.post(f"/admin/users/{member_id}/suspend")
    admin_client.post(f"/admin/users/{member_id}/unsuspend")

    confirm = {"token": token, "password": "a-brand-new-password"}
    assert TestClient(app).post("/auth/password-reset/confirm", json=confirm).status_code == 422


def test_an_admin_demoted_mid_request_gets_403(
    app: FastAPI,
    admin_client: TestClient,
    seed_invite: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _member_client, member_id = _member(app, seed_invite)

    async def no_admins_left(_self: InMemoryUserRepository) -> list[object]:
        return []  # the caller passed require_admin, then lost the role

    monkeypatch.setattr(InMemoryUserRepository, "lock_active_admins", no_admins_left)
    refused = admin_client.post(f"/admin/users/{member_id}/suspend")
    assert refused.status_code == 403 and refused.json()["detail"] == "Admin only."
