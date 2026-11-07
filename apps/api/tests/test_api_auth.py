# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.adapters.passwords import Argon2PasswordHasher
from flashcards.domain.invites import Role
from flashcards.domain.users import User

PASSWORD = "correct-horse-battery"


def _signup(client: TestClient, token: str, email: str, password: str = PASSWORD) -> object:
    return client.post(
        "/auth/signup", json={"token": token, "email": email, "password": password}
    )


def test_signup_sets_cookie_and_me_returns_the_user(
    client: TestClient, seed_invite: Callable[..., str]
) -> None:
    token = seed_invite(role=Role.ADMIN)
    response = _signup(client, token, "admin@example.com")
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "admin@example.com"
    assert body["role"] == "admin"

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "admin@example.com"


def test_signup_with_bad_invite_is_generic_400(client: TestClient) -> None:
    response = _signup(client, "not-a-real-token", "x@example.com")
    assert response.status_code == 400
    assert response.json()["detail"] == "This invite is not valid."


def test_signup_weak_password_is_422(
    client: TestClient, seed_invite: Callable[..., str]
) -> None:
    token = seed_invite()
    response = _signup(client, token, "x@example.com", password="short")
    assert response.status_code == 422


def test_signup_invalid_email_is_422(
    client: TestClient, seed_invite: Callable[..., str]
) -> None:
    token = seed_invite()
    response = _signup(client, token, "not-an-email")
    assert response.status_code == 422


def test_signup_duplicate_email_is_409(
    app: FastAPI, client: TestClient, seed_invite: Callable[..., str]
) -> None:
    first = seed_invite()
    assert _signup(client, first, "dup@example.com").status_code == 201
    second = seed_invite()
    fresh = TestClient(app)
    response = _signup(fresh, second, "dup@example.com")
    assert response.status_code == 409


def test_login_after_signup(
    app: FastAPI, client: TestClient, seed_invite: Callable[..., str]
) -> None:
    token = seed_invite()
    _signup(client, token, "person@example.com")
    fresh = TestClient(app)
    response = fresh.post(
        "/auth/login", json={"email": "person@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert fresh.get("/auth/me").status_code == 200


def test_login_wrong_password_is_401(
    client: TestClient, seed_invite: Callable[..., str]
) -> None:
    token = seed_invite()
    _signup(client, token, "person@example.com")
    response = client.post(
        "/auth/login", json={"email": "person@example.com", "password": "wrong-password-xyz"}
    )
    assert response.status_code == 401


def test_login_unknown_email_is_401(client: TestClient) -> None:
    response = client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )
    assert response.status_code == 401


def test_login_suspended_account_is_403(app: FastAPI, client: TestClient) -> None:
    container = app.state.container
    now = container.clock.now()
    hashed = Argon2PasswordHasher().hash(PASSWORD)
    asyncio.run(
        container.user_repo.add(
            User(
                id="susp-1",
                email="suspended@example.com",
                password_hash=hashed,
                role=Role.MEMBER,
                created_at=now,
                suspended_at=now,
            )
        )
    )
    response = client.post(
        "/auth/login", json={"email": "suspended@example.com", "password": PASSWORD}
    )
    assert response.status_code == 403


def test_me_without_cookie_is_401(client: TestClient) -> None:
    assert client.get("/auth/me").status_code == 401


def test_invalid_cookie_is_401(client: TestClient) -> None:
    client.cookies.set("fc_session", "bogus-token")
    assert client.get("/auth/me").status_code == 401


def test_suspended_user_loses_access_mid_session(
    app: FastAPI, client: TestClient, seed_invite: Callable[..., str]
) -> None:
    token = seed_invite(role=Role.ADMIN)
    _signup(client, token, "admin@example.com")
    container = app.state.container
    user = asyncio.run(container.user_repo.get_by_email("admin@example.com"))
    assert user is not None
    container.user_repo._by_id[user.id] = user.with_suspended(container.clock.now())
    assert client.get("/auth/me").status_code == 401


def test_logout_revokes_the_session(
    client: TestClient, seed_invite: Callable[..., str]
) -> None:
    token = seed_invite()
    _signup(client, token, "person@example.com")
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_list_sessions_marks_current_and_can_revoke_others(
    client: TestClient, seed_invite: Callable[..., str]
) -> None:
    token = seed_invite()
    _signup(client, token, "person@example.com")
    # A second login on the same client creates a second session; the cookie now
    # points at the newer one.
    client.post("/auth/login", json={"email": "person@example.com", "password": PASSWORD})

    sessions = client.get("/auth/sessions").json()
    assert len(sessions) == 2
    current = [s for s in sessions if s["current"]]
    older = [s for s in sessions if not s["current"]]
    assert len(current) == 1
    assert len(older) == 1

    assert client.delete(f"/auth/sessions/{older[0]['id']}").status_code == 204
    assert client.delete(f"/auth/sessions/{older[0]['id']}").status_code == 404


def test_revoke_unknown_session_is_404(
    client: TestClient, seed_invite: Callable[..., str]
) -> None:
    token = seed_invite()
    _signup(client, token, "person@example.com")
    assert client.delete("/auth/sessions/does-not-exist").status_code == 404
