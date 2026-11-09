# SPDX-License-Identifier: AGPL-3.0-only
"""Unsafe requests must come from a trusted origin (ADR 0017)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.api.app import create_app
from flashcards.api.csrf import origin_of
from flashcards.config import Settings

PUBLIC = "https://cards.example.com"
# Any unsafe route works: an empty login body is 422 once past the middleware.
LOGIN = "/auth/login"


@pytest.fixture
def csrf_app() -> FastAPI:
    return create_app(
        Settings(environment="test", session_cookie_secure=False, public_base_url=PUBLIC + "/")
    )


@pytest.fixture
def browser(csrf_app: FastAPI) -> TestClient:
    """A client that sends only what each test sets: no X-Requested-With."""
    client = TestClient(csrf_app, base_url="http://api.internal")
    client.headers.pop("X-Requested-With")
    return client


def _status(client: TestClient, headers: dict[str, str], method: str = "POST") -> int:
    return client.request(method, LOGIN, json={}, headers=headers).status_code


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": PUBLIC},
        {"Origin": "HTTPS://Cards.Example.com"},
        {"Origin": "http://api.internal"},  # the request's own host
        {"Origin": "https://api.internal"},  # own host behind a TLS proxy
        {"Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "none"},
        {"Origin": "null", "Sec-Fetch-Site": "same-origin"},
        {"Referer": PUBLIC + "/decks/1"},
        {"X-Requested-With": "fetch"},
    ],
)
def test_trusted_requests_pass(browser: TestClient, headers: dict[str, str]) -> None:
    assert _status(browser, headers) == 422


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://evil.example.com"},
        {"Origin": "https://sub.cards.example.com"},  # a sibling on the same site
        {"Origin": "http://cards.example.com"},  # the public host over plain http
        {"Origin": "https://evil.example.com", "X-Requested-With": "fetch"},
        {"Origin": "https://evil.example.com", "Sec-Fetch-Site": "same-origin"},
        {"Origin": "null"},
        {"Origin": "null", "X-Requested-With": "fetch"},
        {"Origin": "not a url"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site", "X-Requested-With": "fetch"},
        {"Referer": "https://evil.example.com/page"},
        {"Referer": "https://evil.example.com/page", "X-Requested-With": "fetch"},
    ],
)
def test_untrusted_requests_are_refused(browser: TestClient, headers: dict[str, str]) -> None:
    response = browser.post(LOGIN, json={}, headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-site request refused."}


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_every_unsafe_method_is_checked(browser: TestClient, method: str) -> None:
    assert _status(browser, {"Origin": "https://evil.example.com"}, method) == 403


def test_safe_methods_are_not_checked(browser: TestClient) -> None:
    response = browser.get("/healthz", headers={"Origin": "https://evil.example.com"})
    assert response.status_code == 200


def test_request_without_a_host_header_is_trusted_only_by_the_public_origin(
    csrf_app: FastAPI,
) -> None:
    client = TestClient(csrf_app)
    client.headers.pop("X-Requested-With")
    client.headers.pop("host", None)
    assert _status(client, {"Origin": "http://testserver", "Host": ""}) == 403


def test_a_misconfigured_public_url_trusts_only_same_origin() -> None:
    app = create_app(Settings(environment="test", public_base_url="not-a-url"))
    client = TestClient(app)
    client.headers.pop("X-Requested-With")
    assert _status(client, {"Origin": "http://testserver"}) == 422
    assert _status(client, {"Origin": "https://cards.example.com"}) == 403


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        ("https://Cards.Example.com/path?q=1", "https://cards.example.com"),
        ("http://localhost:5173", "http://localhost:5173"),
        (" https://a.example ", "https://a.example"),
        ("ftp://files.example.com", None),
        ("/relative/path", None),
        ("http://[::1", None),
        ("", None),
    ],
)
def test_origin_of(url: str, origin: str | None) -> None:
    assert origin_of(url) == origin
