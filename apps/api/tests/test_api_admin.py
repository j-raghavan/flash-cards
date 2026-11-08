# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.domain.invites import Role

PASSWORD = "correct-horse-battery"


def test_admin_creates_invite_and_it_works_for_signup(
    app: FastAPI, admin_client: TestClient
) -> None:
    response = admin_client.post("/admin/invites", json={"role": "member"})
    assert response.status_code == 201
    token = response.json()["token"]
    assert response.json()["role"] == "member"

    newcomer = TestClient(app)
    signup = newcomer.post(
        "/auth/signup",
        json={"token": token, "email": "member@example.com", "password": PASSWORD},
    )
    assert signup.status_code == 201


def test_admin_lists_invites_with_status(admin_client: TestClient) -> None:
    admin_client.post("/admin/invites", json={"role": "member", "email": "a@example.com"})
    admin_client.post("/admin/invites", json={"role": "member", "email": "b@example.com"})
    listing = admin_client.get("/admin/invites")
    assert listing.status_code == 200
    by_email = {row["email"]: row for row in listing.json()}
    assert by_email["a@example.com"]["status"] == "active"
    assert by_email["b@example.com"]["status"] == "active"
    # The seeded invite the admin signed up with is now spent.
    assert by_email[None]["status"] == "redeemed"


def test_admin_revokes_invite_then_signup_fails(
    app: FastAPI, admin_client: TestClient
) -> None:
    created = admin_client.post("/admin/invites", json={"role": "member"}).json()
    invite_id = created["id"]
    token = created["token"]

    assert admin_client.post(f"/admin/invites/{invite_id}/revoke").status_code == 204

    newcomer = TestClient(app)
    signup = newcomer.post(
        "/auth/signup",
        json={"token": token, "email": "member@example.com", "password": PASSWORD},
    )
    assert signup.status_code == 400


def test_revoke_unknown_invite_is_404(admin_client: TestClient) -> None:
    assert admin_client.post("/admin/invites/missing/revoke").status_code == 404


def test_admin_endpoints_require_authentication(client: TestClient) -> None:
    assert client.get("/admin/invites").status_code == 401


def test_member_cannot_reach_admin_endpoints(
    app: FastAPI, admin_client: TestClient
) -> None:
    token = admin_client.post("/admin/invites", json={"role": "member"}).json()["token"]
    member = TestClient(app)
    member.post(
        "/auth/signup",
        json={"token": token, "email": "member@example.com", "password": PASSWORD},
    )
    assert member.get("/admin/invites").status_code == 403


def test_invite_ttl_must_be_positive(admin_client: TestClient) -> None:
    response = admin_client.post("/admin/invites", json={"role": "member", "ttl_days": 0})
    assert response.status_code == 422


def test_seeded_admin_carries_admin_role(admin_client: TestClient) -> None:
    assert admin_client.get("/auth/me").json()["role"] == Role.ADMIN.value
