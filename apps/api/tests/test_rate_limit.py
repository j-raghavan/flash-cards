# SPDX-License-Identifier: AGPL-3.0-only
"""The per-address rate limit on sign-in and share routes (ADR 0019)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.types import Scope

from flashcards.api.app import create_app
from flashcards.api.rate_limit import (
    UNKNOWN_CLIENT,
    RateRule,
    TokenBuckets,
    client_key,
    default_rules,
)
from flashcards.config import Settings


class _Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


def _scope(peer: str | None, *forwarded: str) -> Scope:
    return {
        "type": "http",
        "client": None if peer is None else (peer, 1234),
        "headers": [(b"x-forwarded-for", value.encode()) for value in forwarded],
    }


def test_a_bucket_bursts_a_minute_then_refills_at_the_steady_rate() -> None:
    clock = _Clock()
    buckets = TokenBuckets(6, clock=clock)  # one token every 10 seconds
    assert [buckets.take("a") for _ in range(6)] == [None] * 6
    assert buckets.take("a") == pytest.approx(10.0)
    assert buckets.take("b") is None  # other keys have their own bucket
    clock.value += 5
    assert buckets.take("a") == pytest.approx(5.0)
    clock.value += 5
    assert buckets.take("a") is None
    clock.value += 3600  # refilling stops at the capacity
    assert [buckets.take("a") for _ in range(6)] == [None] * 6
    assert buckets.take("a") is not None


def test_buckets_are_bounded_and_forget_the_least_recently_used() -> None:
    clock = _Clock()
    buckets = TokenBuckets(1, max_keys=2, clock=clock)
    assert buckets.take("a") is None
    assert buckets.take("b") is None
    assert buckets.take("a") is not None  # a is now the most recent
    assert buckets.take("c") is None  # forgets b
    assert len(buckets) == 2
    assert buckets.take("b") is None  # b starts afresh; forgets a
    assert buckets.take("c") is not None
    with pytest.raises(ValueError):
        TokenBuckets(0)


def test_rules_match_method_and_path() -> None:
    auth, shared = default_rules(auth_per_minute=1, shared_per_minute=1)
    assert auth.matches("POST", "/auth/login")
    assert not auth.matches("GET", "/auth/me")
    assert not auth.matches("POST", "/decks")
    assert shared.matches("GET", "/shared/tok") and shared.matches("POST", "/shared/t/clone")
    assert RateRule("x", "/x/", 1).matches("DELETE", "/x/1")


def test_forwarded_for_is_ignored_unless_proxies_are_trusted() -> None:
    assert client_key(_scope("10.0.0.1", "6.6.6.6"), 0) == "10.0.0.1"
    assert client_key(_scope(None), 0) == UNKNOWN_CLIENT
    # One proxy appends the address it saw; anything to its left is the client's own.
    assert client_key(_scope("10.0.0.1", "6.6.6.6, 1.2.3.4"), 1) == "1.2.3.4"
    # Two proxies: the outer proxy's entry is second from the right. Headers repeat.
    assert client_key(_scope("10.0.0.1", "6.6.6.6", "1.2.3.4, 10.0.0.2"), 2) == "1.2.3.4"
    # Fewer hops than trusted proxies: the request skipped a proxy, use the peer.
    assert client_key(_scope("10.0.0.1"), 1) == "10.0.0.1"


def test_addresses_are_normalized() -> None:
    # A whole IPv6 /64 is one client; IPv4-mapped addresses count as IPv4.
    assert client_key(_scope("2001:db8::1"), 0) == "2001:db8::/64"
    assert client_key(_scope("2001:db8::ffff:1"), 0) == "2001:db8::/64"
    assert client_key(_scope("::ffff:1.2.3.4"), 0) == "1.2.3.4"
    assert client_key(_scope("10.0.0.1", "x" * 500), 1) == "x" * 64


def _app(trusted_proxy_count: int = 0) -> TestClient:
    settings = Settings(
        environment="test",
        session_cookie_secure=False,
        rate_limit_enabled=True,
        rate_limit_auth_per_minute=3,
        rate_limit_shared_per_minute=2,
        trusted_proxy_count=trusted_proxy_count,
    )
    return TestClient(create_app(settings))


def test_sign_in_routes_answer_429_with_retry_after_once_the_budget_is_spent() -> None:
    client = _app()
    body = {"email": "nobody@example.com", "password": "correct-horse-battery"}
    assert [client.post("/auth/login", json=body).status_code for _ in range(3)] == [401] * 3
    refused = client.post("/auth/password-reset", json={"email": "nobody@example.com"})
    assert refused.status_code == 429
    assert refused.json() == {"detail": "Too many requests. Try again later."}
    assert int(refused.headers["Retry-After"]) >= 1
    # Reads and other routes are not limited.
    assert client.get("/auth/options").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_share_routes_are_limited_separately() -> None:
    with _app() as client:  # runs the lifespan too, which the limiter passes through
        _share_routes_are_limited_separately(client)


def _share_routes_are_limited_separately(client: TestClient) -> None:
    assert [client.get("/shared/nope").status_code for _ in range(2)] == [401, 401]
    assert client.get("/shared/nope").status_code == 429
    assert client.post("/auth/login", json={"email": "a@b.co", "password": "x"}).status_code != 429


def test_a_trusted_proxy_count_limits_each_forwarded_client() -> None:
    client = _app(trusted_proxy_count=1)
    login = {"email": "nobody@example.com", "password": "correct-horse-battery"}

    def attempt(address: str) -> int:
        headers = {"X-Forwarded-For": f"9.9.9.9, {address}"}
        return client.post("/auth/login", json=login, headers=headers).status_code

    assert [attempt("1.1.1.1") for _ in range(3)] == [401] * 3
    assert attempt("1.1.1.1") == 429
    assert attempt("2.2.2.2") == 401  # another client behind the same proxy


def test_the_limit_can_be_turned_off() -> None:
    client = TestClient(
        create_app(Settings(environment="test", rate_limit_enabled=False,
                            rate_limit_shared_per_minute=1))
    )
    assert [client.get("/shared/x").status_code for _ in range(3)] == [401] * 3
