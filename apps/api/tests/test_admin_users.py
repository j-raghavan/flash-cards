# SPDX-License-Identifier: AGPL-3.0-only
"""Admin user management (F-AUTH-4, F-ADMIN-2), each action audited."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

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
