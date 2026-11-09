# SPDX-License-Identifier: AGPL-3.0-only
"""A cap on every request body, enforced before anything parses it.

The multipart parser spools file uploads to temporary files with no size limit,
and it runs before route dependencies, so before authentication. Without a cap
here, one anonymous request could fill the disk; the per-route upload caps only
bound memory once a handler runs. This middleware refuses a declared
Content-Length over the cap at once, and counts the body as it streams in, so
a chunked or lying request is cut off at the cap too (ADR 0017).
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Above the largest upload cap (40 MB .apkg) plus multipart overhead; matches the
# bundled Caddyfile's request_body limit.
MAX_REQUEST_BYTES = 50 * 1024 * 1024
_TOO_LARGE_BODY = json.dumps({"detail": "Request body too large."}).encode()


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_REQUEST_BYTES) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None and declared > self._max_bytes:
            await _too_large(send)
            return

        received = 0
        exceeded = False
        response_started = False

        async def capped_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    exceeded = True
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if exceeded and not response_started:
                return  # whatever the app answers to the cut-off body is replaced by 413
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, capped_receive, guarded_send)
        except Exception:
            if not exceeded or response_started:
                raise
        if exceeded and not response_started:
            await _too_large(send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _too_large(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_TOO_LARGE_BODY)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _TOO_LARGE_BODY})
