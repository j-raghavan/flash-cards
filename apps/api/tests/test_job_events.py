# SPDX-License-Identifier: AGPL-3.0-only
"""Progress events for generation jobs (F-AI-10, ADR 0012)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.api import job_events as events
from flashcards.api.app import create_app
from flashcards.api.container import Container
from flashcards.api.job_events import StreamSlots, job_events, snapshot_id
from flashcards.config import Settings
from flashcards.domain.ai import StructuredRequest, StructuredResult, Thoroughness, TokenUsage
from flashcards.domain.invites import Role
from flashcards.domain.jobs import AiJob, JobNotFound, JobStatus, new_generation_job

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
PASSWORD = "correct-horse-battery"


def _job() -> AiJob:
    return new_generation_job(
        owner_id="o", source_id="s", draft_deck_id="d", thoroughness=Thoroughness.STANDARD,
        now=NOW,
    )


class _Script:
    """Hands out job readings in order and records how the stream waited."""

    def __init__(self, readings: list[AiJob | None]) -> None:
        self.readings = readings
        self.now = 0.0
        self.sleeps: list[float] = []

    async def load(self) -> AiJob | None:
        return self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now


def _parse(frames: list[bytes]) -> list[dict[str, str]]:
    parsed = []
    for frame in frames:
        fields: dict[str, str] = {}
        for line in frame.decode().strip().splitlines():
            key, _, value = line.partition(": ")
            fields[key] = value
        parsed.append(fields)
    return parsed


async def _run(script: _Script, **limits: float) -> list[dict[str, str]]:
    return _parse(
        [
            frame
            async for frame in job_events(
                script.load, sleep=script.sleep, clock=script.clock, **limits
            )
        ]
    )


async def test_progress_on_change_then_done() -> None:
    queued = _job()
    running = replace(queued, status=JobStatus.RUNNING, total_units=2)
    halfway = replace(running, completed_units=1)
    done = replace(halfway, status=JobStatus.SUCCEEDED, completed_units=2)
    script = _Script([queued, running, running, halfway, done])

    frames = await _run(script)

    assert [f["event"] for f in frames] == ["progress", "progress", "progress", "done"]
    assert frames[0]["retry"] == str(events.RECONNECT_MS) and "retry" not in frames[1]
    assert [json.loads(f["data"])["completed_units"] for f in frames] == [0, 0, 1, 2]
    assert frames[-1]["id"] == snapshot_id(done)
    assert json.loads(frames[-1]["data"])["status"] == "succeeded"


async def test_a_finished_job_sends_one_done_event() -> None:
    failed = replace(_job(), status=JobStatus.FAILED, error="boom")
    script = _Script([failed])
    frames = await _run(script)
    assert [f["event"] for f in frames] == ["done"] and script.sleeps == []


async def test_quiet_jobs_get_keepalives_and_streams_end_on_time() -> None:
    script = _Script([_job()])
    frames = await _run(script, poll_seconds=1, keepalive_seconds=3, max_seconds=7)
    assert [f.get("event", f.get(": ping", "ping")) for f in frames] == [
        "progress", "ping", "ping"
    ]
    assert len(script.sleeps) == 7  # stopped at max_seconds


async def test_a_job_that_disappears_ends_the_stream() -> None:
    assert await _run(_Script([None])) == []
    frames = await _run(_Script([_job(), None]))
    assert [f["event"] for f in frames] == ["progress"]


def test_stream_slots_cap_per_user_and_expire() -> None:
    now = [0.0]
    slots = StreamSlots(limit=2, lifetime=10, clock=lambda: now[0])
    first, second = slots.acquire("u"), slots.acquire("u")
    assert first is not None and second is not None
    assert slots.acquire("u") is None and slots.acquire("other") is not None
    slots.release("u", first)
    third = slots.acquire("u")
    assert third is not None
    now[0] = 11.0  # unreleased slots expire, so nobody is locked out for good
    assert slots.acquire("u") is not None
    slots.release("u", 999.0)  # releasing an unknown token is harmless
    slots.release("nobody", 1.0)


class _SlowLLM:
    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        await asyncio.sleep(0.05)
        return StructuredResult(
            data={"cards": [{"type": "basic", "fields": {"front": "Q", "back": "A"}}]},
            usage=TokenUsage(1, 1),
            model="fake",
        )


def _pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"page {i} with enough text to read")
    try:
        return bytes(doc.tobytes())
    finally:
        doc.close()


def _signed_in(app: FastAPI, client: TestClient, email: str = "a@example.com") -> str:
    container: Container = app.state.container
    token = asyncio.run(container.invites.create(created_by="t", role=Role.MEMBER)).raw_token
    body = {"token": token, "email": email, "password": PASSWORD}
    assert client.post("/auth/signup", json=body).status_code == 201
    deck_id: str = client.post("/decks", json={"name": "Deck"}).json()["id"]
    return deck_id


def _start(client: TestClient, deck_id: str, pages: int) -> str:
    job_id: str = client.post(
        "/ai/generate",
        files={"file": ("doc", _pdf(pages), "application/pdf")},
        data={"deck_id": deck_id, "thoroughness": "standard"},
    ).json()["id"]
    return job_id


def _read_stream(client: TestClient, job_id: str) -> list[dict[str, str]]:
    with client.stream("GET", f"/ai/jobs/{job_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = b"".join(response.iter_bytes())
    return _parse([part + b"\n\n" for part in body.split(b"\n\n") if part.strip()])


@pytest.fixture
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "POLL_SECONDS", 0.01)


def test_progress_streams_over_http_until_the_job_finishes(fast_polling: None) -> None:
    app = create_app(Settings(environment="test", session_cookie_secure=False), llm=_SlowLLM())
    with TestClient(app) as client:
        job_id = _start(client, _signed_in(app, client), pages=3)
        frames = _read_stream(client, job_id)

    assert frames[-1]["event"] == "done"
    final = json.loads(frames[-1]["data"])
    assert final["status"] == "succeeded" and final["completed_units"] == 3
    seen = [json.loads(f["data"])["completed_units"] for f in frames]
    assert seen == sorted(seen) and len(set(seen)) > 1  # progress arrived along the way
    assert len({f["id"] for f in frames}) == len(frames)  # one event per distinct state


def test_no_unit_of_work_stays_open_while_streaming(fast_polling: None) -> None:
    app = create_app(Settings(environment="test", session_cookie_secure=False), llm=_SlowLLM())
    real = app.state.services_provider
    open_units: list[int] = [0]
    most_open: list[int] = [0]

    async def counting() -> AsyncIterator[Container]:
        open_units[0] += 1
        most_open[0] = max(most_open[0], open_units[0])
        try:
            async for container in real():
                yield container
        finally:
            open_units[0] -= 1

    app.state.services_provider = counting
    with TestClient(app) as client:
        job_id = _start(client, _signed_in(app, client), pages=2)
        frames = _read_stream(client, job_id)

    assert frames[-1]["event"] == "done"
    # The request's own unit closed before the stream's first poll opened, and
    # polls never overlap: one unit at a time besides the worker's.
    assert open_units[0] == 0 and most_open[0] <= 2


def test_event_stream_access_rules(app: FastAPI, fast_polling: None) -> None:
    client = TestClient(app)
    deck_id = _signed_in(app, client)
    job_id = _start(client, deck_id, pages=1)

    assert TestClient(app).get(f"/ai/jobs/{job_id}/events").status_code == 401
    assert client.get("/ai/jobs/missing/events").status_code == 404
    stranger = TestClient(app)
    _signed_in(app, stranger, email="b@example.com")
    assert stranger.get(f"/ai/jobs/{job_id}/events").status_code == 404

    slots: StreamSlots = app.state.event_stream_slots
    tokens = [slots.acquire(_user_id(client)) for _ in range(events.MAX_STREAMS_PER_USER)]
    refused = client.get(f"/ai/jobs/{job_id}/events")
    assert refused.status_code == 429
    for token in tokens:
        assert token is not None
        slots.release(_user_id(client), token)


def _user_id(client: TestClient) -> str:
    user_id: str = client.get("/auth/me").json()["id"]
    return user_id


class _BrokenLLM:
    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        raise ConnectionError("provider unavailable")


def test_a_finished_job_streams_a_single_done_event(fast_polling: None) -> None:
    app = create_app(
        Settings(environment="test", session_cookie_secure=False), llm=_BrokenLLM()
    )
    with TestClient(app) as client:
        job_id = _start(client, _signed_in(app, client), pages=1)
        for _ in range(300):
            if client.get(f"/ai/jobs/{job_id}").json()["status"] == "failed":
                break
            asyncio.run(asyncio.sleep(0.01))
        frames = _read_stream(client, job_id)
    assert [f["event"] for f in frames] == ["done"]
    assert json.loads(frames[0]["data"])["status"] == "failed"


def test_a_job_deleted_while_watched_ends_the_stream(
    app: FastAPI, fast_polling: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(app)
    job_id = _start(client, _signed_in(app, client), pages=1)
    container: Container = app.state.container
    real_get = container.generation.get
    calls = [0]

    async def gone_after_the_check(*, owner_id: str, job_id: str) -> AiJob:
        calls[0] += 1
        if calls[0] > 1:  # the route's ownership check passes; the stream's poll does not
            raise JobNotFound(job_id)
        return await real_get(owner_id=owner_id, job_id=job_id)

    monkeypatch.setattr(container.generation, "get", gone_after_the_check)
    assert _read_stream(client, job_id) == []
