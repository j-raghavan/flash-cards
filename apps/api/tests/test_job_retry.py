# SPDX-License-Identifier: AGPL-3.0-only
"""Retrying a failed generation job (F-AI-10, ADR 0012)."""

from __future__ import annotations

import asyncio
import time

import pymupdf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.api.app import create_app
from flashcards.config import Settings
from flashcards.domain.ai import StructuredRequest, StructuredResult, TokenUsage
from flashcards.domain.invites import Role

PASSWORD = "correct-horse-battery"


class _FailsOnce:
    """Fails the second call only, i.e. page 2 of the first attempt."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        self.calls += 1
        if self.calls == 2:
            raise ConnectionError("provider unavailable")
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


def _settle(client: TestClient, job_id: str) -> dict[str, object]:
    for _ in range(300):
        job: dict[str, object] = client.get(f"/ai/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not settle")


def _signed_in(app: FastAPI, client: TestClient) -> str:
    token = asyncio.run(app.state.container.invites.create(created_by="t", role=Role.MEMBER))
    body = {"token": token.raw_token, "email": "a@example.com", "password": PASSWORD}
    assert client.post("/auth/signup", json=body).status_code == 201
    deck_id: str = client.post("/decks", json={"name": "Deck"}).json()["id"]
    return deck_id


def test_a_failed_job_resumes_where_it_stopped() -> None:
    llm = _FailsOnce()
    app = create_app(Settings(environment="test", session_cookie_secure=False), llm=llm)
    with TestClient(app) as client:
        deck_id = _signed_in(app, client)
        job_id = client.post(
            "/ai/generate",
            files={"file": ("doc", _pdf(2), "application/pdf")},
            data={"deck_id": deck_id, "thoroughness": "standard"},
        ).json()["id"]
        failed = _settle(client, job_id)
        assert failed["status"] == "failed" and failed["completed_units"] == 1

        retried = client.post(f"/ai/jobs/{job_id}/retry")
        assert retried.status_code == 200 and retried.json()["status"] == "queued"
        assert retried.json()["error"] is None

        done = _settle(client, job_id)
        assert done["status"] == "succeeded" and done["completed_units"] == 2
        assert llm.calls == 3  # page 1 was not generated again
        assert len(client.get(f"/ai/jobs/{job_id}/drafts").json()) == 2  # no duplicates

        again = client.post(f"/ai/jobs/{job_id}/retry")
        assert again.status_code == 409
        assert again.json()["detail"] == "The job cannot do that in its current state."
        assert client.post("/ai/jobs/missing/retry").status_code == 404
        assert TestClient(app).post(f"/ai/jobs/{job_id}/retry").status_code == 401


async def test_jobs_are_held_until_the_unit_of_work_commits() -> None:
    from flashcards.adapters.job_queue import InProcessJobQueue
    from flashcards.api.provider import _AfterCommitQueue

    seen: list[str] = []

    async def record(job_id: str) -> None:
        seen.append(job_id)

    queue = InProcessJobQueue()
    queue.start(record)
    held = _AfterCommitQueue(queue)
    try:
        await held.enqueue("a")
        await held.enqueue("b")
        await queue.join()
        assert seen == []  # nothing reaches the worker before the commit
        await held.release()
        await queue.join()
        assert seen == ["a", "b"]
        await held.release()  # releasing again sends nothing twice
        await queue.join()
        assert seen == ["a", "b"]
    finally:
        await queue.stop()
