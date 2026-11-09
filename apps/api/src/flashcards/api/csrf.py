# SPDX-License-Identifier: AGPL-3.0-only
"""Cross-site request forgery protection for state-changing requests (NF-SEC-4).

SameSite=Lax keeps the session cookie off most cross-site requests, but not off
requests from a sibling subdomain of the same site. This middleware refuses an
unsafe request (POST, PUT, PATCH, DELETE) with 403 unless it shows that it came
from a trusted origin. The rule, checked in order (ADR 0017):

1. ``Origin`` present and not ``null``: allowed only if it is a trusted origin.
2. ``Sec-Fetch-Site`` present: allowed only if it is ``same-origin`` or ``none``.
3. ``Referer`` present: allowed only if its origin is trusted.
4. None of these: allowed only with an ``X-Requested-With`` header. Browsers
   cannot send that header cross-origin without a CORS preflight, which this API
   never approves, so only same-origin scripts and non-browser clients can.

Trusted origins are the public origin (FLASHCARDS_PUBLIC_BASE_URL) and the
request's own origin, taken from its Host header. Safe methods pass untouched.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SAME_ORIGIN_FETCH_SITES = frozenset({"same-origin", "none"})
_DEFAULT_PORTS = {"http": 80, "https": 443}
_FORBIDDEN_BODY = json.dumps({"detail": "Cross-site request refused."}).encode()


def origin_of(url: str) -> str | None:
    """The lower-cased scheme://host[:port] of an absolute http(s) URL, else None."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    # Browsers omit a scheme's default port from Origin, so a configured
    # https://host:443 must compare equal to https://host.
    default_port = _DEFAULT_PORTS[parts.scheme]
    if port == default_port:
        origin = origin.removesuffix(f":{default_port}")
    return origin


class CsrfOriginMiddleware:
    def __init__(self, app: ASGIApp, *, public_base_url: str) -> None:
        self._app = app
        self._public_origin = origin_of(public_base_url)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in UNSAFE_METHODS:
            await self._app(scope, receive, send)
            return
        headers = {
            name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in scope["headers"]
        }
        if self._allowed(headers):
            await self._app(scope, receive, send)
            return
        await _forbidden(send)

    def _allowed(self, headers: dict[str, str]) -> bool:
        origin = headers.get("origin")
        if origin is not None and origin.strip().lower() != "null":
            return self._trusted(origin_of(origin), headers.get("host"))
        fetch_site = headers.get("sec-fetch-site")
        if fetch_site is not None:
            return fetch_site.strip().lower() in _SAME_ORIGIN_FETCH_SITES
        if origin is not None:
            return False  # an opaque origin with nothing else to vouch for it
        referer = headers.get("referer")
        if referer is not None:
            return self._trusted(origin_of(referer), headers.get("host"))
        return "x-requested-with" in headers

    def _trusted(self, origin: str | None, host: str | None) -> bool:
        if origin is None:
            return False
        if origin == self._public_origin:
            return True
        # Same origin as the request itself. The scheme is not compared: behind a
        # TLS-terminating proxy the API sees plain HTTP while the browser sent
        # https. The Secure, SameSite cookie already keeps http pages out.
        return host is not None and urlsplit(origin).netloc == host.strip().lower()


async def _forbidden(send: Send) -> None:
    start: Message = {
        "type": "http.response.start",
        "status": 403,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(_FORBIDDEN_BODY)).encode()),
        ],
    }
    await send(start)
    await send({"type": "http.response.body", "body": _FORBIDDEN_BODY})
