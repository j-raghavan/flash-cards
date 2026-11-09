# SPDX-License-Identifier: AGPL-3.0-only
"""Every request body is capped before anything parses or spools it (ADR 0017)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from flashcards.api import body_limit
from flashcards.api.app import create_app
from flashcards.api.body_limit import MAX_REQUEST_BYTES, BodySizeLimitMiddleware
from flashcards.config import Settings

CAP = 100


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    @app.post("/upload")
    async def upload(file: UploadFile = File(...)) -> dict[str, int]:
        return {"size": len(await file.read())}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=CAP)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app())


def test_a_body_within_the_cap_passes(client: TestClient) -> None:
    response = client.post("/echo", content=b"x" * CAP)
    assert response.status_code == 200 and response.json() == {"size": CAP}


def test_a_declared_length_over_the_cap_is_refused_unread(client: TestClient) -> None:
    response = client.post("/echo", content=b"x" * (CAP + 1))
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large."}


def test_a_streamed_body_is_cut_off_at_the_cap(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(10):
            yield b"x" * 40  # no Content-Length: sent chunked

    assert client.post("/echo", content=chunks()).status_code == 413


def test_a_multipart_upload_over_the_cap_is_refused_before_parsing(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b"--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"f\"\r\n\r\n"
        for _ in range(10):
            yield b"x" * 40
        yield b"\r\n--b--\r\n"

    response = client.post(
        "/upload",
        content=chunks(),
        headers={"Content-Type": "multipart/form-data; boundary=b"},
    )
    assert response.status_code == 413


def test_an_unparseable_content_length_is_counted_instead(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b"x" * (CAP + 1)

    response = client.post("/echo", content=chunks(), headers={"Content-Length": "lots"})
    assert response.status_code in (400, 413)


def test_safe_requests_and_lifespan_pass_through(client: TestClient) -> None:
    with client:
        assert client.get("/healthz").status_code == 200


async def _run(app: ASGIApp, messages: list[Message]) -> list[Message]:
    """Drive the middleware directly with a fixed sequence of incoming messages."""
    incoming = iter(messages)
    sent: list[Message] = []

    async def receive() -> Message:
        return next(incoming)

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {"type": "http", "method": "POST", "path": "/", "headers": []}
    await BodySizeLimitMiddleware(app, max_bytes=CAP)(scope, receive, send)
    return sent


async def test_a_limit_hit_after_the_response_started_is_not_masked() -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await receive()

    body: Message = {"type": "http.request", "body": b"x" * (CAP + 1), "more_body": False}
    with pytest.raises(body_limit._BodyTooLarge):  # surfaces as a server error
        await _run(app, [body])


async def test_a_disconnect_message_passes_through() -> None:
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append((await receive())["type"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent = await _run(app, [{"type": "http.disconnect"}])
    assert seen == ["http.disconnect"]
    assert sent[0]["status"] == 204


async def test_an_app_error_unrelated_to_the_limit_is_raised() -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        await _run(app, [])


def test_the_app_caps_request_bodies() -> None:
    app = create_app(Settings(environment="test"))
    caps = [m for m in app.user_middleware if cast(object, m.cls) is BodySizeLimitMiddleware]
    assert len(caps) == 1
    assert MAX_REQUEST_BYTES == 50 * 1024 * 1024
