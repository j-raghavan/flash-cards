# SPDX-License-Identifier: AGPL-3.0-only
"""The YouTube transcript adapter (F-AI-2, ADR 0020), offline.

The library runs for real; only the bottom of the transport is replaced, so the
guard (hosts, redirects, timeouts, deadline, size caps) is exercised on every
request. Responses come from recorded fixtures trimmed to the fields the library
reads. Nothing here touches the network.
"""

from __future__ import annotations

import io
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NoReturn

import anyio
import pytest
from pydantic import ValidationError
from requests import PreparedRequest, Request, Response
from requests.adapters import HTTPAdapter
from requests.structures import CaseInsensitiveDict

from flashcards.adapters import offload, youtube_transcripts
from flashcards.adapters.transcripts import NullTranscriptProvider
from flashcards.adapters.youtube_transcripts import (
    DEFAULT_LANGUAGES,
    MAX_REQUESTS,
    MAX_RESPONSE_BYTES,
    FetchRefused,
    GuardedYouTubeAdapter,
    Timeout,
    YouTubeTranscriptProvider,
    _Run,
)
from flashcards.api.app import _transcripts_from_settings
from flashcards.config import Settings
from flashcards.domain.transcripts import (
    InvalidVideoUrl,
    Transcript,
    TranscriptSegment,
    TranscriptUnavailable,
)

VID = "dQw4w9WgXcQ"
FIXTURES = Path(__file__).parent / "fixtures" / "youtube"
WATCH = f"https://www.youtube.com/watch?v={VID}"
PLAYER = "https://www.youtube.com/youtubei/v1/player"
CAPTIONS = "https://www.youtube.com/api/timedtext"


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate({"environment": "test", **overrides})


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class Reply:
    def __init__(
        self, body: bytes = b"", status: int = 200, headers: Mapping[str, str] | None = None
    ) -> None:
        self.body = body
        self.status = status
        self.headers = dict(headers or {})


Route = Callable[[PreparedRequest], Reply]


def _recorded(request: PreparedRequest) -> Reply:
    url = request.url or ""
    if url.startswith(WATCH):
        return Reply(_fixture("watch.html"))
    if url.startswith(PLAYER):
        return Reply(_fixture("player.json"), headers={"Content-Type": "application/json"})
    if url.startswith(CAPTIONS) and "lang=en" in url:
        return Reply(_fixture("timedtext_en.xml"))
    if url.startswith(CAPTIONS) and "lang=de" in url:
        return Reply(_fixture("timedtext_de.xml"))
    return Reply(b"not found", status=404)


class FakeTransportAdapter(GuardedYouTubeAdapter):
    """The real guard over a canned transport that records what reached it."""

    def __init__(
        self,
        route: Route = _recorded,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_requests: int = MAX_REQUESTS,
    ) -> None:
        super().__init__(timeout_seconds=7.0, clock=clock, max_requests=max_requests)
        self.route = route
        self.sent: list[str] = []
        self.timeouts: list[Timeout] = []
        self.verify: list[bool | str] = []

    def _transmit(
        self,
        request: PreparedRequest,
        *,
        timeout: Timeout,
        verify: bool | str,
        cert: object,
        proxies: Mapping[str, str] | None,
    ) -> Response:
        self.sent.append(request.url or "")
        self.timeouts.append(timeout)
        self.verify.append(verify)
        return _response(request, self.route(request))


def _response(request: PreparedRequest, reply: Reply) -> Response:
    response = Response()
    response.status_code = reply.status
    response.raw = io.BytesIO(reply.body)
    response.url = request.url or ""
    response.request = request
    response.headers = CaseInsensitiveDict(reply.headers)
    response.encoding = "utf-8"
    return response


def _provider(
    adapter: FakeTransportAdapter, *, languages: tuple[str, ...] = DEFAULT_LANGUAGES
) -> YouTubeTranscriptProvider:
    return YouTubeTranscriptProvider(languages=languages, adapter_factory=lambda: adapter)


async def test_fetches_the_preferred_language_from_youtube_only() -> None:
    adapter = FakeTransportAdapter()
    transcript = await _provider(adapter).fetch(VID)

    assert transcript == Transcript(
        video_id=VID,
        segments=(
            TranscriptSegment(0.32, "Welcome to the first lecture on cell biology."),
            TranscriptSegment(3.42, "Mitochondria make most of the cell's ATP."),
            # The whitespace-only caption is dropped; markup is stripped.
            TranscriptSegment(8.62, "The ribosome builds proteins."),
        ),
    )
    assert [url.split("?")[0] for url in adapter.sent] == [
        "https://www.youtube.com/watch",
        PLAYER,
        CAPTIONS,
    ]
    assert adapter.timeouts == [(7.0, 7.0)] * 3
    assert adapter.verify == [True] * 3


async def test_a_generated_track_in_a_preferred_language_is_used() -> None:
    transcript = await _provider(FakeTransportAdapter(), languages=("de", "en")).fetch(VID)
    assert transcript.segments == (TranscriptSegment(0.32, "Willkommen zur ersten Vorlesung."),)


async def test_without_a_preferred_language_any_track_is_used() -> None:
    transcript = await _provider(FakeTransportAdapter(), languages=("fr",)).fetch(VID)
    assert transcript.segments[0].text.startswith("Welcome")  # the manual track first


def _player_with(captions: object) -> Route:
    def route(request: PreparedRequest) -> Reply:
        if (request.url or "").startswith(PLAYER):
            body = b'{"playabilityStatus": {"status": "OK"}, "captions": %s}' % captions
            return Reply(body)
        return _recorded(request)

    return route


@pytest.mark.parametrize(
    "captions",
    [
        b"{}",  # transcripts disabled
        b'{"playerCaptionsTracklistRenderer": {"captionTracks": []}}',  # none at all
    ],
)
async def test_a_video_without_captions_has_no_transcript(captions: bytes) -> None:
    with pytest.raises(TranscriptUnavailable):
        await _provider(FakeTransportAdapter(_player_with(captions))).fetch(VID)


async def test_a_caption_url_outside_youtube_is_never_requested() -> None:
    track = (
        b'{"playerCaptionsTracklistRenderer": {"captionTracks": [{"baseUrl": '
        b'"https://169.254.169.254/latest/meta-data?lang=en", "name": {"runs": [{"text": "x"}]},'
        b' "languageCode": "en"}]}}'
    )
    adapter = FakeTransportAdapter(_player_with(track))
    with pytest.raises(TranscriptUnavailable):
        await _provider(adapter).fetch(VID)
    assert all(url.startswith("https://www.youtube.com/") for url in adapter.sent)
    assert len(adapter.sent) == 2  # the watch page and the player, nothing else


async def test_a_redirect_away_from_youtube_is_refused() -> None:
    def route(request: PreparedRequest) -> Reply:
        return Reply(status=302, headers={"Location": "http://internal.example/admin"})

    adapter = FakeTransportAdapter(route)
    with pytest.raises(TranscriptUnavailable):
        await _provider(adapter).fetch(VID)
    assert adapter.sent == [WATCH]


async def test_an_oversized_response_is_cut_off() -> None:
    def route(request: PreparedRequest) -> Reply:
        return Reply(b"x" * (MAX_RESPONSE_BYTES + 1))

    with pytest.raises(TranscriptUnavailable):
        await _provider(FakeTransportAdapter(route)).fetch(VID)


async def test_a_fetch_may_make_only_so_many_requests() -> None:
    adapter = FakeTransportAdapter(max_requests=2)
    with pytest.raises(TranscriptUnavailable):
        await _provider(adapter).fetch(VID)
    assert len(adapter.sent) == 2


async def test_a_fetch_past_its_deadline_stops() -> None:
    ticks = iter([0.0, 1.0, 1.0, 100.0, 100.0, 100.0])
    adapter = FakeTransportAdapter(clock=lambda: next(ticks, 100.0))
    with pytest.raises(TranscriptUnavailable):
        await _provider(adapter).fetch(VID)
    assert len(adapter.sent) == 1


async def test_a_blocked_server_reports_no_transcript() -> None:
    adapter = FakeTransportAdapter(lambda _request: Reply(status=429))
    with pytest.raises(TranscriptUnavailable):
        await _provider(adapter).fetch(VID)


async def test_an_all_blank_transcript_is_no_transcript() -> None:
    def route(request: PreparedRequest) -> Reply:
        if (request.url or "").startswith(CAPTIONS):
            return Reply(b'<transcript><text start="0" dur="1"> </text></transcript>')
        return _recorded(request)

    with pytest.raises(TranscriptUnavailable, match="empty"):
        await _provider(FakeTransportAdapter(route)).fetch(VID)


async def test_a_malformed_video_id_never_reaches_the_network() -> None:
    adapter = FakeTransportAdapter()
    with pytest.raises(InvalidVideoUrl):
        await _provider(adapter).fetch("../../etc/x")
    assert adapter.sent == []


async def test_a_fetch_runs_off_the_event_loop() -> None:
    threads: list[int] = []

    def route(request: PreparedRequest) -> Reply:
        threads.append(threading.get_ident())
        return _recorded(request)

    await _provider(FakeTransportAdapter(route)).fetch(VID)
    assert threads and threading.get_ident() not in threads


async def test_when_every_slot_is_busy_the_fetch_is_turned_away() -> None:
    limiter = anyio.CapacityLimiter(1)
    adapter = FakeTransportAdapter()
    provider = YouTubeTranscriptProvider(
        limiter=limiter, slot_wait_seconds=0.01, adapter_factory=lambda: adapter
    )
    other_request = object()
    await limiter.acquire_on_behalf_of(other_request)
    try:
        with pytest.raises(TranscriptUnavailable, match="busy"):
            await provider.fetch(VID)
    finally:
        limiter.release_on_behalf_of(other_request)
    assert adapter.sent == []
    assert limiter.borrowed_tokens == 0


async def test_the_slot_is_released_after_a_failure() -> None:
    limiter = anyio.CapacityLimiter(1)
    provider = YouTubeTranscriptProvider(
        limiter=limiter,
        adapter_factory=lambda: FakeTransportAdapter(lambda _r: Reply(status=500)),
    )
    with pytest.raises(TranscriptUnavailable):
        await provider.fetch(VID)
    assert limiter.borrowed_tokens == 0


def test_the_fetch_limiter_is_small_and_separate() -> None:
    assert offload.TRANSCRIPT_FETCH.total_tokens == offload.TRANSCRIPT_FETCH_SLOTS == 2
    assert offload.TRANSCRIPT_FETCH is not offload.PARSING


@pytest.mark.parametrize(
    "url",
    [
        f"http://www.youtube.com/watch?v={VID}",  # not https
        "https://www.youtube.com.evil.example/watch",  # lookalike host
        "https://www.youtube.com:8443/watch",  # another port
        "https://user:pw@www.youtube.com/watch",  # credentials
        "https://127.0.0.1/watch",
        "https://youtu.be/x",  # a link host, never fetched
    ],
)
def test_the_guard_refuses_anything_but_https_youtube(url: str) -> None:
    adapter = FakeTransportAdapter()
    request = Request("GET", url).prepare()
    with pytest.raises(FetchRefused):
        adapter.send(request)
    assert adapter.sent == []


def test_the_guard_streams_with_its_own_timeouts_and_always_verifies_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_send(
        self: HTTPAdapter,
        request: PreparedRequest,
        stream: bool = False,
        timeout: Timeout = None,
        verify: bool | str = True,
        cert: object = None,
        proxies: Mapping[str, str] | None = None,
    ) -> Response:
        seen.update(stream=stream, timeout=timeout, verify=verify)
        return _response(request, Reply(b"ok"))

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    adapter = GuardedYouTubeAdapter(timeout_seconds=4.0)
    response = adapter.send(Request("GET", WATCH).prepare(), timeout=999, verify=False)
    assert response.content == b"ok"
    assert seen == {"stream": True, "timeout": (4.0, 4.0), "verify": True}


def test_the_default_transport_uses_the_configured_timeout() -> None:
    provider = YouTubeTranscriptProvider(timeout_seconds=3.0, languages=())
    adapter = provider._adapter_factory()
    assert isinstance(adapter, GuardedYouTubeAdapter)
    assert adapter._timeout == 3.0
    assert provider._languages == DEFAULT_LANGUAGES


# --- YouTube transcript settings (ADR 0020) ---


def test_transcript_retrieval_is_off_by_default() -> None:
    settings = _settings()
    assert settings.youtube_transcripts_enabled is False
    assert isinstance(_transcripts_from_settings(settings), NullTranscriptProvider)


def test_turning_transcript_retrieval_on_wires_the_youtube_adapter() -> None:
    provider = _transcripts_from_settings(
        _settings(
            youtube_transcripts_enabled=True,
            youtube_transcript_languages="de, en",
            youtube_transcript_timeout_seconds=4,
        )
    )
    assert isinstance(provider, YouTubeTranscriptProvider)
    assert provider._languages == ("de", "en")


@pytest.mark.parametrize(
    "overrides",
    [
        {"youtube_transcript_languages": "english"},
        {"youtube_transcript_languages": ""},
        {"youtube_transcript_timeout_seconds": 0},
        {"youtube_transcript_timeout_seconds": 61},
    ],
)
def test_bad_transcript_settings_fail_at_startup(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _settings(**overrides)


async def test_a_hung_fetch_stops_the_request_but_keeps_its_slot_until_the_thread_ends() -> None:
    release = threading.Event()

    def route(request: PreparedRequest) -> Reply:
        release.wait(5)
        return _recorded(request)

    limiter = anyio.CapacityLimiter(1)
    provider = YouTubeTranscriptProvider(
        limiter=limiter,
        deadline_seconds=0.05,
        adapter_factory=lambda: FakeTransportAdapter(route),
    )
    with anyio.fail_after(5), pytest.raises(TranscriptUnavailable, match="too long"):
        await provider.fetch(VID)  # gives up after the deadline plus the 2 s grace
    assert limiter.borrowed_tokens == 1  # the abandoned thread still holds it
    release.set()
    with anyio.fail_after(5):
        while limiter.borrowed_tokens:
            await anyio.sleep(0.01)


def test_a_thread_that_starts_after_the_request_gave_up_does_nothing() -> None:
    adapter = FakeTransportAdapter()
    provider = _provider(adapter)
    run = _Run()
    assert run.abandon()
    with pytest.raises(TranscriptUnavailable):
        provider._run_in_thread(VID, run)
    assert adapter.sent == []
    assert not run.abandon()  # decided once


def test_a_thread_outside_the_event_loop_still_finishes() -> None:
    # Releasing through the event loop fails when there is none (at shutdown).
    run = _Run()
    segments = _provider(FakeTransportAdapter())._run_in_thread(VID, run)
    assert segments[0].text.startswith("Welcome")
    assert not run.abandon()


async def test_a_request_that_gives_up_before_its_thread_starts_frees_the_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_starts(*_args: object, **_kwargs: object) -> NoReturn:
        await anyio.sleep_forever()
        raise AssertionError("cancelled before this")

    monkeypatch.setattr(youtube_transcripts, "DEADLINE_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(anyio.to_thread, "run_sync", never_starts)
    limiter = anyio.CapacityLimiter(1)
    adapter = FakeTransportAdapter()
    provider = YouTubeTranscriptProvider(
        limiter=limiter, deadline_seconds=0.01, adapter_factory=lambda: adapter
    )
    with pytest.raises(TranscriptUnavailable, match="too long"):
        await provider.fetch(VID)
    assert limiter.borrowed_tokens == 0
    assert adapter.sent == []
