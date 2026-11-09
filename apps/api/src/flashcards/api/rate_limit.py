# SPDX-License-Identifier: AGPL-3.0-only
"""A per-address rate limit for the unauthenticated and sign-in routes (ADR 0019).

Every POST under /auth/ (sign-in, sign-up, invite sign-up, password reset,
email verification and its resend, and any later sign-in step) and every
request under /shared/ (share preview and clone) draws from a token bucket kept
per client address. An empty bucket answers 429 with Retry-After, before the
request reaches a route, so a refused request costs no database work or Argon2.

The limiter lives in this process's memory: each API process limits on its own,
and a restart forgets the counts. A reverse proxy can add a global limit in
front (see docs/ops/runbook.md). Memory is bounded: each rule keeps at most
`max_keys` buckets and forgets the least recently used one when full. An
attacker with more addresses than that can make their own bucket be forgotten,
but such an attacker gets past any per-address limit anyway.

The client address is the connection's peer, as the ASGI server reports it,
unless `trusted_proxies` is set: then it is the address that many hops from the
right of X-Forwarded-For, the one the outermost trusted proxy saw. Headers are
never trusted by default, so a client cannot pick its own bucket. IPv6 clients
are keyed by their /64 network, since one host commonly holds a whole /64.
"""

from __future__ import annotations

import ipaddress
import json
import math
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send

_TOO_MANY = json.dumps({"detail": "Too many requests. Try again later."}).encode()
UNKNOWN_CLIENT = "unknown"
DEFAULT_MAX_KEYS = 10_000


@dataclass(frozen=True, slots=True)
class RateRule:
    """Requests matching `prefix` (and `methods`, when given) share one budget."""

    name: str
    prefix: str
    per_minute: int
    methods: frozenset[str] | None = None

    def matches(self, method: str, path: str) -> bool:
        return path.startswith(self.prefix) and (self.methods is None or method in self.methods)


def default_rules(*, auth_per_minute: int, shared_per_minute: int) -> tuple[RateRule, ...]:
    return (
        RateRule("auth", "/auth/", auth_per_minute, frozenset({"POST"})),
        RateRule("shared", "/shared/", shared_per_minute),
    )


class TokenBuckets:
    """Token buckets per key, refilled continuously, at most `max_keys` of them.

    A bucket holds up to a minute's worth of requests and refills at the same
    rate, so a client can burst a minute's allowance and then continue at the
    steady rate.
    """

    def __init__(
        self,
        per_minute: int,
        *,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if per_minute < 1 or max_keys < 1:
            raise ValueError("per_minute and max_keys must be at least 1")
        self._capacity = float(per_minute)
        self._rate = per_minute / 60.0  # tokens per second
        self._max_keys = max_keys
        self._clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._buckets)

    def take(self, key: str) -> float | None:
        """Spend one token for `key`: None when allowed, else seconds until one is free."""
        now = self._clock()
        tokens, updated = self._buckets.pop(key, (self._capacity, now))
        tokens = min(self._capacity, tokens + (now - updated) * self._rate)
        if tokens >= 1.0:
            tokens -= 1.0
            wait = None
        else:
            wait = (1.0 - tokens) / self._rate
        self._buckets[key] = (tokens, now)  # most recently used last
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)
        return wait


def client_key(scope: Scope, trusted_proxies: int) -> str:
    """The address a request is limited under (see the module docstring)."""
    peer = scope.get("client")
    address: str = peer[0] if peer else UNKNOWN_CLIENT
    if trusted_proxies > 0:
        hops = _forwarded_for(scope)
        if len(hops) >= trusted_proxies:
            address = hops[-trusted_proxies]
    return _normalize(address)


def _route_path(scope: Scope) -> str:
    """The path the routes see: without the root path when the server was given one."""
    path: str = scope["path"]
    root: str = scope.get("root_path", "")
    if root and path.startswith(root):
        return path[len(root) :] or "/"
    return path


def _forwarded_for(scope: Scope) -> list[str]:
    hops: list[str] = []
    for name, value in scope.get("headers", ()):
        if name == b"x-forwarded-for":
            hops.extend(part.strip() for part in value.decode("latin-1").split(","))
    return hops


def _normalize(address: str) -> str:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        # Not an address (a test client's name, or a garbled header): use it as is,
        # bounded in length so a header cannot grow memory per key.
        return address[:64]
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network((ip, 64), strict=False))
    return str(ip)


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        rules: tuple[RateRule, ...],
        trusted_proxies: int = 0,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._app = app
        self._rules = rules
        self._trusted_proxies = trusted_proxies
        self._buckets = {
            rule.name: TokenBuckets(rule.per_minute, max_keys=max_keys, clock=clock)
            for rule in rules
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            method: str = scope["method"]
            path = _route_path(scope)
            rule = next((r for r in self._rules if r.matches(method, path)), None)
            if rule is not None:
                wait = self._buckets[rule.name].take(client_key(scope, self._trusted_proxies))
                if wait is not None:
                    await _too_many(send, wait)
                    return
        await self._app(scope, receive, send)


async def _too_many(send: Send, wait: float) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_TOO_MANY)).encode()),
                # Rounded up, so a client that waits this long is never early.
                (b"retry-after", str(max(1, math.ceil(wait))).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _TOO_MANY})
