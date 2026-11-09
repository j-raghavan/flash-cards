# SPDX-License-Identifier: AGPL-3.0-only
"""Two-factor sign-in over HTTP (F-AUTH-3): second step, management, admin reset."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from soft_authenticator import SoftAuthenticator

from flashcards.api.app import _secret_box_from_settings
from flashcards.api.container import Container
from flashcards.api.two_factor_schemas import MAX_CREDENTIAL_JSON
from flashcards.config import Settings

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


def _code(secret: str, offset: int = 0) -> str:
    return pyotp.TOTP(secret).at(datetime.now(UTC), counter_offset=offset)


def _enable_totp(client: TestClient) -> tuple[str, list[str]]:
    setup = client.post("/auth/two-factor/totp", json={"password": PASSWORD})
    assert setup.status_code == 200
    secret: str = setup.json()["secret"]
    confirmed = client.post("/auth/two-factor/totp/confirm", json={"code": _code(secret)})
    assert confirmed.status_code == 200
    codes: list[str] = confirmed.json()["recovery_codes"]
    return secret, codes


def _add_passkey(client: TestClient, device: SoftAuthenticator) -> dict[str, object]:
    started = client.post("/auth/two-factor/passkeys/options", json={"password": PASSWORD})
    assert started.status_code == 200
    body = started.json()
    added = client.post(
        "/auth/two-factor/passkeys",
        json={
            "token": body["token"],
            "credential": device.register(body["options"]),
            "name": "Key",
        },
    )
    assert added.status_code == 201
    result: dict[str, object] = added.json()
    return result


def _first_step(app: FastAPI) -> tuple[TestClient, str]:
    fresh = TestClient(app)
    login = fresh.post("/auth/login", json={"email": MEMBER, "password": PASSWORD})
    assert login.status_code == 202
    assert "fc_session" not in login.cookies  # no session before the second factor
    token: str = login.json()["token"]
    return fresh, token


def test_authenticator_app_sign_in(app: FastAPI, seed_invite: Callable[..., str]) -> None:
    member, _ = _member(app, seed_invite)
    assert member.get("/auth/two-factor").json() == {
        "enabled": False,
        "totp": False,
        "totp_available": True,
        "passkeys": [],
        "recovery_codes_left": 0,
    }
    assert (
        member.post("/auth/two-factor/totp", json={"password": "wrong"}).status_code == 403
    )
    setup = member.post("/auth/two-factor/totp", json={"password": PASSWORD}).json()
    assert setup["uri"].startswith("otpauth://totp/Flash-Cards:")
    wrong = "000000" if _code(setup["secret"]) != "000000" else "111111"
    assert member.post("/auth/two-factor/totp/confirm", json={"code": wrong}).status_code == 422
    confirmed = member.post("/auth/two-factor/totp/confirm", json={"code": _code(setup["secret"])})
    assert len(confirmed.json()["recovery_codes"]) == 10
    assert member.post("/auth/two-factor/totp/confirm", json={"code": "1"}).status_code == 409
    assert member.post("/auth/two-factor/totp", json={"password": PASSWORD}).status_code == 409
    assert member.get("/auth/two-factor").json()["enabled"] is True

    fresh, token = _first_step(app)
    assert fresh.post("/auth/login").status_code == 422  # body required
    login = TestClient(app).post("/auth/login", json={"email": MEMBER, "password": PASSWORD})
    assert login.json()["methods"] == ["totp", "recovery_code"]
    bad = fresh.post("/auth/login/second-factor", json={"token": token, "code": "000000"})
    assert bad.status_code == 401 and bad.json()["detail"] == "That code is not right."
    assert fresh.get("/auth/me").status_code == 401
    good = fresh.post(
        "/auth/login/second-factor", json={"token": token, "code": _code(setup["secret"], 1)}
    )
    assert good.status_code == 200 and good.json()["email"] == MEMBER
    assert fresh.get("/auth/me").status_code == 200
    again = fresh.post(
        "/auth/login/second-factor", json={"token": token, "code": _code(setup["secret"], 1)}
    )
    assert again.status_code == 401 and "expired" in again.json()["detail"]


def test_recovery_codes_and_disabling(app: FastAPI, seed_invite: Callable[..., str]) -> None:
    member, _ = _member(app, seed_invite)
    assert (
        member.post("/auth/two-factor/recovery-codes", json={"password": PASSWORD}).status_code
        == 409
    )
    assert member.request(
        "DELETE", "/auth/two-factor/totp", json={"password": PASSWORD}
    ).status_code == 409
    _secret, codes = _enable_totp(member)
    fresh, token = _first_step(app)
    used = fresh.post("/auth/login/second-factor", json={"token": token, "code": codes[3]})
    assert used.status_code == 200
    assert member.get("/auth/two-factor").json()["recovery_codes_left"] == 9
    wrong = member.post("/auth/two-factor/recovery-codes", json={"password": "nope"})
    assert wrong.status_code == 403
    fresh_codes = member.post("/auth/two-factor/recovery-codes", json={"password": PASSWORD})
    assert len(fresh_codes.json()["recovery_codes"]) == 10
    assert member.request(
        "DELETE", "/auth/two-factor/totp", json={"password": "nope"}
    ).status_code == 403
    assert member.request(
        "DELETE", "/auth/two-factor/totp", json={"password": PASSWORD}
    ).status_code == 204
    login = TestClient(app).post("/auth/login", json={"email": MEMBER, "password": PASSWORD})
    assert login.status_code == 200  # one step again


def test_passkey_sign_in(app: FastAPI, seed_invite: Callable[..., str]) -> None:
    member, _ = _member(app, seed_invite)
    device = SoftAuthenticator()
    assert (
        member.post("/auth/two-factor/passkeys/options", json={"password": "x"}).status_code == 403
    )
    added = _add_passkey(member, device)
    assert isinstance(added["recovery_codes"], list) and len(added["recovery_codes"]) == 10
    passkeys = member.get("/auth/two-factor").json()["passkeys"]
    assert [p["name"] for p in passkeys] == ["Key"] and passkeys[0]["last_used_at"] is None

    fresh, token = _first_step(app)
    options = fresh.post("/auth/login/passkey/options", json={"token": token}).json()["options"]
    forged = device.assert_(options, origin="https://evil.example")
    refused = fresh.post("/auth/login/passkey", json={"token": token, "credential": forged})
    assert refused.status_code == 401
    options = fresh.post("/auth/login/passkey/options", json={"token": token}).json()["options"]
    signed = fresh.post(
        "/auth/login/passkey", json={"token": token, "credential": device.assert_(options)}
    )
    assert signed.status_code == 200 and fresh.get("/auth/me").status_code == 200
    again = fresh.post("/auth/login/passkey", json={"token": token, "credential": {}})
    assert again.status_code == 401 and "expired" in again.json()["detail"]
    assert member.get("/auth/two-factor").json()["passkeys"][0]["last_used_at"] is not None

    passkey_id = passkeys[0]["id"]
    assert member.request(
        "DELETE", f"/auth/two-factor/passkeys/{passkey_id}", json={"password": "x"}
    ).status_code == 403
    assert member.request(
        "DELETE", "/auth/two-factor/passkeys/ghost", json={"password": PASSWORD}
    ).status_code == 404
    assert member.request(
        "DELETE", f"/auth/two-factor/passkeys/{passkey_id}", json={"password": PASSWORD}
    ).status_code == 204
    assert member.get("/auth/two-factor").json()["enabled"] is False


def test_passkey_registration_errors(app: FastAPI, seed_invite: Callable[..., str]) -> None:
    member, _ = _member(app, seed_invite)
    started = member.post("/auth/two-factor/passkeys/options", json={"password": PASSWORD}).json()
    bad = member.post(
        "/auth/two-factor/passkeys", json={"token": started["token"], "credential": {"id": "x"}}
    )
    assert bad.status_code == 422
    expired = member.post(
        "/auth/two-factor/passkeys", json={"token": "unknown", "credential": {"id": "x"}}
    )
    assert expired.status_code == 409
    huge = {"blob": "x" * MAX_CREDENTIAL_JSON}
    too_big = member.post(
        "/auth/two-factor/passkeys", json={"token": started["token"], "credential": huge}
    )
    assert too_big.status_code == 422
    for _ in range(10):
        _add_passkey(member, SoftAuthenticator())
    full = member.post("/auth/two-factor/passkeys/options", json={"password": PASSWORD})
    assert full.status_code == 409


def test_second_step_errors(app: FastAPI, seed_invite: Callable[..., str]) -> None:
    member, _ = _member(app, seed_invite)
    _enable_totp(member)
    fresh, token = _first_step(app)
    no_passkey = fresh.post("/auth/login/passkey/options", json={"token": token})
    assert no_passkey.status_code == 404
    unknown = fresh.post("/auth/login/passkey/options", json={"token": "unknown"})
    assert unknown.status_code == 401
    assert (
        fresh.post("/auth/login/second-factor", json={"token": "x", "code": "1"}).status_code
        == 401
    )
    refused = fresh.post("/auth/login/passkey", json={"token": token, "credential": {}})
    assert refused.status_code == 401 and refused.json()["detail"].startswith("The passkey")


def test_failed_codes_lock_sign_in(app: FastAPI, seed_invite: Callable[..., str]) -> None:
    member, _ = _member(app, seed_invite)
    _enable_totp(member)
    fresh, token = _first_step(app)
    for _ in range(4):
        fresh.post("/auth/login/second-factor", json={"token": token, "code": "000000"})
    locked = fresh.post("/auth/login/second-factor", json={"token": token, "code": "000000"})
    assert locked.status_code == 429 and int(locked.headers["Retry-After"]) > 0
    assert (
        fresh.post("/auth/login/passkey", json={"token": token, "credential": {}}).status_code
        == 429
    )
    # Proofs for changes count against the same lock.
    for path, method in (
        ("/auth/two-factor/totp", "DELETE"),
        ("/auth/two-factor/recovery-codes", "POST"),
        ("/auth/two-factor/passkeys/options", "POST"),
    ):
        response = member.request(method, path, json={"password": PASSWORD})
        assert response.status_code == 429, path


def test_passkey_removal_is_locked_too(app: FastAPI, seed_invite: Callable[..., str]) -> None:
    member, _ = _member(app, seed_invite)
    added = _add_passkey(member, SoftAuthenticator())
    passkey = added["passkey"]
    assert isinstance(passkey, dict)
    for _ in range(5):
        member.post("/auth/two-factor/recovery-codes", json={"password": "wrong"})
    response = member.request(
        "DELETE", f"/auth/two-factor/passkeys/{passkey['id']}", json={"password": PASSWORD}
    )
    assert response.status_code == 429
    assert member.post("/auth/two-factor/totp", json={"password": PASSWORD}).status_code == 429


def test_suspension_between_the_steps(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> None:
    member, member_id = _member(app, seed_invite)
    secret, _ = _enable_totp(member)
    fresh, token = _first_step(app)
    assert admin_client.post(f"/admin/users/{member_id}/suspend").status_code == 200
    blocked = fresh.post(
        "/auth/login/second-factor", json={"token": token, "code": _code(secret, 1)}
    )
    assert blocked.status_code == 403


def test_suspension_blocks_a_passkey_finish(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> None:
    member, member_id = _member(app, seed_invite)
    device = SoftAuthenticator()
    _add_passkey(member, device)
    fresh, token = _first_step(app)
    options = fresh.post("/auth/login/passkey/options", json={"token": token}).json()["options"]
    admin_client.post(f"/admin/users/{member_id}/suspend")
    blocked = fresh.post(
        "/auth/login/passkey", json={"token": token, "credential": device.assert_(options)}
    )
    assert blocked.status_code == 403


def test_admin_sees_and_resets_two_factor(
    app: FastAPI, admin_client: TestClient, seed_invite: Callable[..., str]
) -> None:
    member, member_id = _member(app, seed_invite)
    _enable_totp(member)
    users = {u["id"]: u for u in admin_client.get("/admin/users").json()}
    assert users[member_id]["two_factor"] is True
    assert admin_client.post(f"/admin/users/{member_id}/unlock").json()["two_factor"] is None
    assert member.post(f"/admin/users/{member_id}/two-factor/reset").status_code == 403
    assert admin_client.post("/admin/users/ghost/two-factor/reset").status_code == 404
    reset = admin_client.post(f"/admin/users/{member_id}/two-factor/reset")
    assert reset.status_code == 200 and reset.json()["two_factor"] is False
    events = admin_client.get("/admin/audit").json()
    assert events[0]["action"] == "two_factor.reset" and events[0]["target_id"] == member_id
    login = TestClient(app).post("/auth/login", json={"email": MEMBER, "password": PASSWORD})
    assert login.status_code == 200


def test_account_deletion_removes_second_factors(
    app: FastAPI, seed_invite: Callable[..., str]
) -> None:
    member, member_id = _member(app, seed_invite)
    _enable_totp(member)
    container: Container = app.state.container
    assert asyncio.run(container.two_factor.enabled_among([member_id])) == {member_id}
    assert member.delete("/account").status_code == 204
    assert asyncio.run(container.two_factor.enabled_among([member_id])) == set()


def test_no_key_means_no_authenticator_apps(
    app: FastAPI, seed_invite: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    member, _ = _member(app, seed_invite)
    container: Container = app.state.container
    monkeypatch.setattr(container.two_factor, "_box", None)
    response = member.post("/auth/two-factor/totp", json={"password": PASSWORD})
    assert response.status_code == 409
    assert member.get("/auth/two-factor").json()["totp_available"] is False


def test_secret_box_from_settings(caplog: pytest.LogCaptureFixture) -> None:
    key = Fernet.generate_key().decode()
    box = _secret_box_from_settings(Settings(totp_encryption_keys=key), persistent=True)
    assert box is not None and box.open(box.seal("s")) == "s"
    assert _secret_box_from_settings(Settings(), persistent=False) is None
    with caplog.at_level(logging.WARNING):
        assert _secret_box_from_settings(Settings(), persistent=True) is None
    assert "FLASHCARDS_TOTP_ENCRYPTION_KEYS" in caplog.text
