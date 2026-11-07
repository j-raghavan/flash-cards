# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from fastapi.testclient import TestClient

from flashcards.api.app import create_app
from flashcards.config import Settings

PASSWORD = "correct-horse-battery"


def test_no_bootstrap_token_by_default() -> None:
    app = create_app(Settings(environment="development"))
    assert app.state.bootstrap_invite_token is None


def test_dev_bootstrap_seeds_a_usable_admin_invite() -> None:
    app = create_app(
        Settings(
            environment="development", dev_bootstrap_invite=True, session_cookie_secure=False
        )
    )
    token = app.state.bootstrap_invite_token
    assert token is not None

    client = TestClient(app)
    response = client.post(
        "/auth/signup",
        json={"token": token, "email": "admin@example.com", "password": PASSWORD},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "admin"


def test_dev_bootstrap_is_ignored_in_production() -> None:
    app = create_app(Settings(environment="production", dev_bootstrap_invite=True))
    assert app.state.bootstrap_invite_token is None
