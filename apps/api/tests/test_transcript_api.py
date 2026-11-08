# SPDX-License-Identifier: AGPL-3.0-only
"""YouTube and pasted-transcript generation, over HTTP and end to end (F-AI-2)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.api.app import create_app
from flashcards.api.container import Container, build_container
from flashcards.config import Settings
from flashcards.domain.ai import StructuredRequest, StructuredResult, Thoroughness, TokenUsage
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.invites import Role
from flashcards.domain.jobs import JobStatus
from flashcards.domain.transcripts import Transcript, TranscriptSegment

PASSWORD = "correct-horse-battery"
VID = "dQw4w9WgXcQ"


class _FakeLLM:
    def __init__(self) -> None:
        self.requests: list[StructuredRequest] = []

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        self.requests.append(request)
        return StructuredResult(
            data={"cards": [{"type": "basic", "fields": {"front": "Q", "back": "A"}}]},
            usage=TokenUsage(1, 1),
            model="fake",
        )


def _member(app: FastAPI) -> TestClient:
    container: Container = app.state.container
    token = asyncio.run(container.invites.create(created_by="seed", role=Role.MEMBER)).raw_token
    client = TestClient(app)
    resp = client.post(
        "/auth/signup", json={"token": token, "email": "m@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 201
    return client


@pytest.fixture
def llm() -> _FakeLLM:
    return _FakeLLM()


@pytest.fixture
def app(llm: _FakeLLM) -> FastAPI:
    return create_app(Settings(environment="test", session_cookie_secure=False), llm=llm)


def _deck(client: TestClient) -> str:
    deck_id: str = client.post("/decks", json={"name": "Lecture"}).json()["id"]
    return deck_id


def test_pasted_transcript_generates_drafts_end_to_end(app: FastAPI, llm: _FakeLLM) -> None:
    client = _member(app)
    deck_id = _deck(client)
    resp = client.post(
        "/ai/generate/transcript",
        json={
            "deck_id": deck_id,
            "transcript": "Mitochondria make ATP.",
            "thoroughness": "concise",
        },
    )
    assert resp.status_code == 202
    job_id = resp.json()["id"]

    # The TestClient does not run the lifespan worker; drive the job directly.
    container: Container = app.state.container
    asyncio.run(container.card_generation.run(job_id))

    job = client.get(f"/ai/jobs/{job_id}").json()
    assert job["status"] == JobStatus.SUCCEEDED.value
    drafts = client.get(f"/ai/jobs/{job_id}/drafts").json()
    assert len(drafts) == 1 and drafts[0]["source_page"] == 1
    # The pasted text reached the model as a data document.
    assert llm.requests[0].documents[0].data == b"Mitochondria make ATP."


def test_blank_transcript_is_422(app: FastAPI) -> None:
    client = _member(app)
    resp = client.post(
        "/ai/generate/transcript", json={"deck_id": _deck(client), "transcript": "   "}
    )
    assert resp.status_code == 422


def test_transcript_over_the_page_cap_is_413(app: FastAPI) -> None:
    client = _member(app)
    # ~25 chunks at 6,000 chars each exceeds the 20-page cap.
    resp = client.post(
        "/ai/generate/transcript",
        json={"deck_id": _deck(client), "transcript": "word " * 30_000},
    )
    assert resp.status_code == 413


def test_transcript_unknown_deck_is_404(app: FastAPI) -> None:
    client = _member(app)
    resp = client.post("/ai/generate/transcript", json={"deck_id": "nope", "transcript": "x"})
    assert resp.status_code == 404


def test_youtube_without_a_fetcher_points_to_paste(app: FastAPI) -> None:
    client = _member(app)
    resp = client.post(
        "/ai/generate/youtube",
        json={"deck_id": _deck(client), "url": f"https://youtu.be/{VID}"},
    )
    assert resp.status_code == 422
    assert "Paste the transcript" in resp.json()["detail"]


def test_youtube_rejects_a_non_youtube_link(app: FastAPI) -> None:
    client = _member(app)
    resp = client.post(
        "/ai/generate/youtube",
        json={"deck_id": _deck(client), "url": "https://example.com/video"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Not a YouTube video link."


def test_youtube_checks_the_deck_before_fetching(app: FastAPI) -> None:
    client = _member(app)
    resp = client.post(
        "/ai/generate/youtube", json={"deck_id": "nope", "url": f"https://youtu.be/{VID}"}
    )
    assert resp.status_code == 404


def test_generation_routes_require_auth(app: FastAPI) -> None:
    anon = TestClient(app)
    assert anon.post("/ai/generate/youtube", json={"deck_id": "d", "url": "u"}).status_code == 401
    assert (
        anon.post("/ai/generate/transcript", json={"deck_id": "d", "transcript": "t"}).status_code
        == 401
    )


class _FakeTranscripts:
    def __init__(self) -> None:
        self.fetched: list[str] = []

    async def fetch(self, video_id: str) -> Transcript:
        self.fetched.append(video_id)
        return Transcript(
            video_id=video_id,
            segments=(TranscriptSegment(0.0, "Intro."), TranscriptSegment(65.0, "Main point.")),
        )


async def test_youtube_with_a_fetcher_enqueues_a_timestamped_job() -> None:
    llm = _FakeLLM()
    transcripts = _FakeTranscripts()
    container = build_container(llm=llm, transcripts=transcripts)
    deck = await container.decks.create(owner_id="alice", name="Talk")

    job = await container.transcript_import.from_youtube(
        owner_id="alice",
        deck_id=deck.id,
        url=f"https://www.youtube.com/watch?v={VID}",
        thoroughness=Thoroughness.STANDARD,
    )
    await container.card_generation.run(job.id)

    assert transcripts.fetched == [VID]
    finished = await container.generation.get(owner_id="alice", job_id=job.id)
    assert finished.status is JobStatus.SUCCEEDED
    # The chunk handed to the model carries its time range.
    assert llm.requests[0].documents[0].data == b"[00:00:00 - 00:01:05]\nIntro. Main point."


def test_youtube_with_a_fetcher_is_202_over_http(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    container: Container = app.state.container
    monkeypatch.setattr(container.transcript_import, "_transcripts", _FakeTranscripts())
    client = _member(app)
    resp = client.post(
        "/ai/generate/youtube",
        json={"deck_id": _deck(client), "url": f"https://youtu.be/{VID}"},
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == JobStatus.QUEUED.value


async def test_youtube_for_someone_elses_deck_never_fetches() -> None:
    transcripts = _FakeTranscripts()
    container = build_container(llm=_FakeLLM(), transcripts=transcripts)
    deck = await container.decks.create(owner_id="alice", name="Talk")
    with pytest.raises(DeckNotFound):
        await container.transcript_import.from_youtube(
            owner_id="bob",
            deck_id=deck.id,
            url=f"https://youtu.be/{VID}",
            thoroughness=Thoroughness.STANDARD,
        )
    assert transcripts.fetched == []
