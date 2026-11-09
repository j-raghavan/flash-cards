# SPDX-License-Identifier: AGPL-3.0-only
"""Automatic YouTube transcript retrieval (F-AI-2, ADR 0020).

Built on youtube-transcript-api (MIT), which talks to YouTube through a blocking
requests Session. Every request it makes goes through GuardedYouTubeAdapter:

- https only, to a fixed set of YouTube hosts, checked on every redirect hop too,
  so neither the user nor a caption URL in YouTube's answer can point the server
  anywhere else (SSRF);
- a connect and read timeout on each request, an overall deadline and a request
  count for the whole fetch;
- a byte cap on each response body, counted after decompression.

The fetch runs in a worker thread under the TRANSCRIPT_FETCH limiter (ADR 0017).
Waiting for a slot is bounded, so a burst is turned away instead of queueing
requests that hold database connections. Any failure becomes the domain's
TranscriptUnavailable, and the user is pointed at the manual paste.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Literal
from urllib.parse import urlsplit

import anyio
import anyio.from_thread
import anyio.to_thread
from requests import PreparedRequest, Response, Session
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from youtube_transcript_api import NoTranscriptFound, TranscriptList, YouTubeTranscriptApi
from youtube_transcript_api import Transcript as AvailableTranscript

from flashcards.adapters.offload import TRANSCRIPT_FETCH
from flashcards.domain.transcripts import (
    Transcript,
    TranscriptSegment,
    TranscriptUnavailable,
    validate_video_id,
)

logger = logging.getLogger(__name__)

# The only hosts the fetch may contact: the watch page, the player API and the
# caption track URLs YouTube hands back all live on these.
ALLOWED_HOSTS = frozenset({"www.youtube.com", "youtube.com", "m.youtube.com"})
# A watch page is about 1.5 MB; a long caption track a few hundred KB.
MAX_RESPONSE_BYTES = 5_000_000
# Watch page (twice with the consent cookie), player API and one caption track,
# with room for a redirect.
MAX_REQUESTS = 6
MAX_REDIRECTS = 2
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_SLOT_WAIT_SECONDS = 5.0
# How long past the fetch deadline the request still waits for the thread.
DEADLINE_GRACE_SECONDS = 2.0
DEFAULT_LANGUAGES = ("en",)
# Small reads, so the deadline is checked often while a body streams in.
_CHUNK_BYTES = 8 * 1024

type Timeout = float | tuple[float, float] | tuple[float, None] | None
type Cert = bytes | str | tuple[bytes | str, bytes | str] | None


class FetchRefused(RequestException):
    """The guard refused a request or a response."""


class GuardedYouTubeAdapter(HTTPAdapter):
    """The transport for one fetch: allowlisted hosts, timeouts, a deadline and size caps.

    One instance per fetch, since it counts requests and time from its creation.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        deadline_seconds: float | None = None,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        max_requests: int = MAX_REQUESTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(max_retries=0)
        self._timeout = timeout_seconds
        self._deadline = clock() + (
            deadline_seconds if deadline_seconds is not None else 3 * timeout_seconds
        )
        self._max_bytes = max_response_bytes
        self._max_requests = max_requests
        self._clock = clock
        self._requests = 0

    def send(
        self,
        request: PreparedRequest,
        stream: bool = False,
        timeout: Timeout = None,
        verify: bool | str = True,
        cert: Cert = None,
        proxies: Mapping[str, str] | None = None,
    ) -> Response:
        self._admit(request.url or "")
        # The caller's stream and timeout are ignored: the body is always streamed so
        # it can be capped, and the timeouts are this adapter's. TLS is always verified.
        response = self._transmit(
            request,
            timeout=(self._timeout, self._timeout),
            verify=True if verify is False else verify,
            cert=cert,
            proxies=proxies,
        )
        try:
            response._content = self._read_capped(response)
        finally:
            response.close()
        return response

    def _transmit(
        self,
        request: PreparedRequest,
        *,
        timeout: Timeout,
        verify: bool | str,
        cert: Cert,
        proxies: Mapping[str, str] | None,
    ) -> Response:
        return super().send(
            request, stream=True, timeout=timeout, verify=verify, cert=cert, proxies=proxies
        )

    def _admit(self, url: str) -> None:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.hostname not in ALLOWED_HOSTS
            or parts.port not in (None, 443)
            or parts.username is not None
            or parts.password is not None
        ):
            raise FetchRefused("refused a request outside YouTube")
        self._requests += 1
        if self._requests > self._max_requests:
            raise FetchRefused("too many requests for one transcript")
        self._check_deadline()

    def _read_capped(self, response: Response) -> bytes:
        body = bytearray()
        for chunk in response.iter_content(_CHUNK_BYTES):
            body.extend(chunk)
            if len(body) > self._max_bytes:
                raise FetchRefused("response too large")
            self._check_deadline()
        return bytes(body)

    def _check_deadline(self) -> None:
        if self._clock() > self._deadline:
            raise FetchRefused("transcript fetch took too long")


class _Run:
    """One fetch's slot, held by its worker thread once that thread starts.

    Decides, under a lock, whether the thread or the request releases the slot:
    the thread when it started, the request when it gave up before that.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: Literal["pending", "started", "abandoned"] = "pending"

    def start(self) -> bool:
        with self._lock:
            if self._state == "abandoned":
                return False
            self._state = "started"
            return True

    def abandon(self) -> bool:
        """True when the thread never started, so the caller must release the slot."""
        with self._lock:
            if self._state == "pending":
                self._state = "abandoned"
                return True
            return False


class YouTubeTranscriptProvider:
    """The TranscriptProvider port over youtube-transcript-api."""

    def __init__(
        self,
        *,
        languages: Sequence[str] = DEFAULT_LANGUAGES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        slot_wait_seconds: float = DEFAULT_SLOT_WAIT_SECONDS,
        deadline_seconds: float | None = None,
        limiter: anyio.CapacityLimiter = TRANSCRIPT_FETCH,
        adapter_factory: Callable[[], GuardedYouTubeAdapter] | None = None,
    ) -> None:
        self._languages = tuple(languages) or DEFAULT_LANGUAGES
        self._slot_wait = slot_wait_seconds
        self._deadline = deadline_seconds if deadline_seconds is not None else 3 * timeout_seconds
        self._limiter = limiter
        self._adapter_factory = adapter_factory or (
            lambda: GuardedYouTubeAdapter(
                timeout_seconds=timeout_seconds, deadline_seconds=self._deadline
            )
        )

    async def fetch(self, video_id: str) -> Transcript:
        validate_video_id(video_id)  # never trust the caller with what goes into a URL
        run = _Run()
        try:
            with anyio.fail_after(self._slot_wait):
                await self._limiter.acquire_on_behalf_of(run)
        except TimeoutError as exc:
            logger.warning("YouTube transcript fetch refused: every slot is busy")
            raise TranscriptUnavailable("transcript retrieval is busy") from exc
        try:
            # The adapter checks the deadline between reads, but one read can trickle
            # on past it, so the request stops waiting at the deadline (plus a little)
            # and leaves the thread behind. The thread keeps the slot until it ends,
            # so abandoned fetches still count against TRANSCRIPT_FETCH.
            with anyio.fail_after(self._deadline + DEADLINE_GRACE_SECONDS):
                segments = await anyio.to_thread.run_sync(
                    self._run_in_thread, video_id, run, abandon_on_cancel=True
                )
        except TimeoutError as exc:
            logger.warning("YouTube transcript for %s took too long", video_id)
            raise TranscriptUnavailable("transcript retrieval took too long") from exc
        finally:
            if run.abandon():  # the thread never started: the slot is still ours
                self._limiter.release_on_behalf_of(run)
        return Transcript(video_id=video_id, segments=segments)

    def _run_in_thread(self, video_id: str, run: _Run) -> tuple[TranscriptSegment, ...]:
        if not run.start():
            raise TranscriptUnavailable("the request went away")
        try:
            return self._fetch_blocking(video_id)
        finally:
            # The event loop may be gone at shutdown; then nothing is waiting anyway.
            with contextlib.suppress(RuntimeError):
                anyio.from_thread.run_sync(self._limiter.release_on_behalf_of, run)

    def _fetch_blocking(self, video_id: str) -> tuple[TranscriptSegment, ...]:
        # A Session per fetch: the library's client is not thread-safe.
        session = Session()
        session.max_redirects = MAX_REDIRECTS
        adapter = self._adapter_factory()
        session.mount("https://", adapter)
        session.mount("http://", adapter)  # mounted only so the guard refuses it
        try:
            listing = YouTubeTranscriptApi(http_client=session).list(video_id)
            fetched = self._choose(listing).fetch()
        except TranscriptUnavailable:
            raise
        except Exception as exc:  # the library raises many types; all mean "none"
            logger.warning("no YouTube transcript for %s: %s", video_id, type(exc).__name__)
            raise TranscriptUnavailable("no transcript could be retrieved") from exc
        finally:
            session.close()
        segments = tuple(
            TranscriptSegment(start=snippet.start, text=snippet.text.strip())
            for snippet in fetched
            if snippet.text.strip()
        )
        if not segments:
            raise TranscriptUnavailable("the transcript is empty")
        return segments

    def _choose(self, listing: TranscriptList) -> AvailableTranscript:
        """The preferred language first (manual before generated), else any track."""
        try:
            return listing.find_transcript(self._languages)
        except NoTranscriptFound:
            for available in listing:
                return available
        raise TranscriptUnavailable("the video has no transcript")
