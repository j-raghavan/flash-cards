# SPDX-License-Identifier: AGPL-3.0-only
"""Video and pasted transcripts as a card-generation source (F-AI-2, F-AI-5).

A transcript is stored as a small versioned envelope so the existing generation
pipeline (quota, async worker, drafts, approval) handles it like any other
source. The worker splits it into time-windowed chunks, each one a "page" whose
text carries its timestamp range, so generated cards stay grounded in where they
came from. Everything here is pure; fetching a transcript sits behind a port.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

TRANSCRIPT_MEDIA_TYPE = "application/vnd.flashcards.transcript+json"
ENVELOPE_PREFIX = b"FLASHCARDS-TRANSCRIPT/1\n"
# Bound the whole transcript and each generation chunk (roughly two hours of speech
# fits in the default 20-page cap at this chunk size).
MAX_TRANSCRIPT_CHARS = 200_000
CHUNK_CHARS = 6_000

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_PATH_PREFIXES = ("/shorts/", "/embed/", "/live/")


class InvalidVideoUrl(Exception):
    """The URL is not a recognised YouTube video link."""


class TranscriptUnavailable(Exception):
    """No transcript could be retrieved for the video."""


class InvalidTranscript(Exception):
    """The transcript is empty, too long, or a malformed envelope."""


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start: float  # seconds from the start of the video
    text: str


@dataclass(frozen=True, slots=True)
class Transcript:
    video_id: str | None  # None for a pasted transcript with no video
    segments: tuple[TranscriptSegment, ...]


@dataclass(frozen=True, slots=True)
class TranscriptChunk:
    index: int  # 1-based, used as the generated cards' source page
    start: float
    end: float
    text: str


def parse_youtube_video_id(url: str) -> str:
    """Return the 11-character video id from a YouTube link, or raise InvalidVideoUrl."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise InvalidVideoUrl("the link must be an http(s) URL")
    host = (parsed.hostname or "").lower()
    candidate: str | None = None
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif host in _YOUTUBE_HOSTS:
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        else:
            for prefix in _PATH_PREFIXES:
                if parsed.path.startswith(prefix):
                    candidate = parsed.path[len(prefix) :].split("/")[0]
                    break
    if candidate is None or not _VIDEO_ID.match(candidate):
        raise InvalidVideoUrl("not a recognised YouTube video link")
    return candidate


def pasted_transcript(text: str) -> Transcript:
    """A transcript typed or pasted by the user, with no timing information."""
    stripped = text.strip()
    if not stripped:
        raise InvalidTranscript("the transcript is empty")
    return Transcript(video_id=None, segments=(TranscriptSegment(start=0.0, text=stripped),))


def validate_transcript(transcript: Transcript) -> None:
    total = sum(len(segment.text) for segment in transcript.segments)
    if total == 0 or not any(segment.text.strip() for segment in transcript.segments):
        raise InvalidTranscript("the transcript is empty")
    if total > MAX_TRANSCRIPT_CHARS:
        raise InvalidTranscript(f"the transcript is too long (max {MAX_TRANSCRIPT_CHARS} chars)")


def encode_transcript(transcript: Transcript) -> bytes:
    validate_transcript(transcript)
    body = {
        "video_id": transcript.video_id,
        "segments": [[segment.start, segment.text] for segment in transcript.segments],
    }
    return ENVELOPE_PREFIX + json.dumps(body).encode("utf-8")


def is_transcript_envelope(data: bytes) -> bool:
    return data.startswith(ENVELOPE_PREFIX)


def decode_transcript(data: bytes) -> Transcript:
    """Parse an envelope. The bytes may come from an upload, so validate everything."""
    if not is_transcript_envelope(data):
        raise InvalidTranscript("not a transcript envelope")
    try:
        body: object = json.loads(data[len(ENVELOPE_PREFIX) :])
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidTranscript("malformed transcript envelope") from exc
    if not isinstance(body, dict):
        raise InvalidTranscript("malformed transcript envelope")
    video_id = body.get("video_id")
    raw_segments = body.get("segments")
    if (video_id is not None and not isinstance(video_id, str)) or not isinstance(
        raw_segments, list
    ):
        raise InvalidTranscript("malformed transcript envelope")
    segments: list[TranscriptSegment] = []
    for item in raw_segments:
        if not isinstance(item, list) or len(item) != 2:
            raise InvalidTranscript("malformed transcript segment")
        start, text = item
        if isinstance(start, bool) or not isinstance(start, int | float) or start < 0:
            raise InvalidTranscript("malformed transcript segment")
        if not isinstance(text, str):
            raise InvalidTranscript("malformed transcript segment")
        segments.append(TranscriptSegment(start=float(start), text=text))
    transcript = Transcript(video_id=video_id, segments=tuple(segments))
    validate_transcript(transcript)
    return transcript


def chunk_transcript(transcript: Transcript, max_chars: int = CHUNK_CHARS) -> list[TranscriptChunk]:
    """Group segments into chunks of at most max_chars, splitting over-long segments."""
    chunks: list[TranscriptChunk] = []
    parts: list[str] = []
    size = 0
    start = end = 0.0

    def flush() -> None:
        chunks.append(
            TranscriptChunk(index=len(chunks) + 1, start=start, end=end, text=" ".join(parts))
        )

    for segment in transcript.segments:
        for piece in _pieces(segment.text.strip(), max_chars):
            if parts and size + 1 + len(piece) > max_chars:
                flush()
                parts, size = [], 0
            if not parts:
                start = segment.start
            parts.append(piece)
            size += len(piece) + (1 if size else 0)
            end = segment.start
    if parts:
        flush()
    return chunks


def chunk_page_text(transcript: Transcript, chunk: TranscriptChunk) -> str:
    """The text handed to the model for one chunk: a time range header for videos."""
    if transcript.video_id is None:
        return chunk.text
    return f"[{_clock(chunk.start)} - {_clock(chunk.end)}]\n{chunk.text}"


def _pieces(text: str, max_chars: int) -> list[str]:
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)] if text else []


def _clock(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"
