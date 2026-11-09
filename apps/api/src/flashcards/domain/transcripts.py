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
import math
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

TRANSCRIPT_MEDIA_TYPE = "application/vnd.flashcards.transcript+json"
ENVELOPE_PREFIX = b"FLASHCARDS-TRANSCRIPT/1\n"
CHUNK_CHARS = 6_000
# The whole transcript must fit the default 20-page generation cap at one chunk per
# page, so a too-long paste gets a clear "too long" instead of "too many pages".
MAX_TRANSCRIPT_CHARS = 20 * CHUNK_CHARS
# The envelope can arrive as an upload, so bound it before parsing: the byte size
# (generous for 120k chars of escaped JSON), the segment count and the timestamps.
MAX_ENVELOPE_BYTES = 2_000_000
MAX_SEGMENTS = 50_000
MAX_START_SECONDS = 7 * 24 * 3600

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
_PATH_PREFIXES = ("/shorts/", "/embed/", "/live/", "/v/")
# A BCP 47 style language tag such as "en", "de" or "pt-BR", as YouTube names tracks.
_LANGUAGE_TAG = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8}){0,2}")
MAX_PREFERRED_LANGUAGES = 10


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
        if parsed.path.rstrip("/") == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        else:
            for prefix in _PATH_PREFIXES:
                if parsed.path.startswith(prefix):
                    candidate = parsed.path[len(prefix) :].split("/")[0]
                    break
    if candidate is None:
        raise InvalidVideoUrl("not a recognised YouTube video link")
    return validate_video_id(candidate)


def validate_video_id(video_id: str) -> str:
    """Return the id if it is exactly 11 URL-safe characters, or raise InvalidVideoUrl.

    fullmatch, not match: "$" also matches before a trailing newline, so a link whose
    v= value ends in %0A would otherwise pass with the newline still attached.
    """
    if not _VIDEO_ID.fullmatch(video_id):
        raise InvalidVideoUrl("not a recognised YouTube video link")
    return video_id


def parse_language_preference(value: str) -> tuple[str, ...]:
    """Comma-separated language tags, most preferred first, e.g. "de,en"."""
    tags = tuple(tag.strip() for tag in value.split(",") if tag.strip())
    if not tags:
        raise ValueError("name at least one transcript language, e.g. en")
    if len(tags) > MAX_PREFERRED_LANGUAGES:
        raise ValueError(f"name at most {MAX_PREFERRED_LANGUAGES} transcript languages")
    for tag in tags:
        if not _LANGUAGE_TAG.fullmatch(tag):
            raise ValueError(f"not a language tag: {tag!r}")
    return tags


def pasted_transcript(text: str) -> Transcript:
    """A transcript typed or pasted by the user, with no timing information."""
    stripped = text.strip()
    if not stripped:
        raise InvalidTranscript("the transcript is empty")
    return Transcript(video_id=None, segments=(TranscriptSegment(start=0.0, text=stripped),))


def validate_transcript(transcript: Transcript) -> None:
    """Applies to pasted, fetched and uploaded transcripts alike."""
    if len(transcript.segments) > MAX_SEGMENTS:
        raise InvalidTranscript(f"the transcript has too many segments (max {MAX_SEGMENTS})")
    for segment in transcript.segments:
        if not math.isfinite(segment.start) or not 0 <= segment.start <= MAX_START_SECONDS:
            raise InvalidTranscript("a transcript timestamp is out of range")
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
    if len(data) > MAX_ENVELOPE_BYTES:
        raise InvalidTranscript("the transcript envelope is too large")
    try:
        # NaN and Infinity are not JSON; reject them rather than let them through.
        body: object = json.loads(data[len(ENVELOPE_PREFIX) :], parse_constant=_no_constants)
    except (ValueError, RecursionError) as exc:  # bad JSON, bad UTF-8, deep nesting
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
        if isinstance(start, bool) or not isinstance(start, int | float):
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

    segments = transcript.segments
    for position, segment in enumerate(segments):
        # A segment ends where the next one starts; the last one's end is unknown,
        # so its own start stands in.
        has_next = position + 1 < len(segments)
        segment_end = segments[position + 1].start if has_next else segment.start
        for piece in _pieces(segment.text.strip(), max_chars):
            if parts and size + 1 + len(piece) > max_chars:
                flush()
                parts, size = [], 0
            if not parts:
                start = segment.start
            parts.append(piece)
            size += len(piece) + (1 if size else 0)
            end = segment_end
    if parts:
        flush()
    return chunks


def chunk_page_text(transcript: Transcript, chunk: TranscriptChunk) -> str:
    """The text handed to the model for one chunk: a time range header for videos."""
    if transcript.video_id is None:
        return chunk.text
    return f"[{_clock(chunk.start)} - {_clock(chunk.end)}]\n{chunk.text}"


def _pieces(text: str, max_chars: int) -> list[str]:
    # Skip pieces that are only whitespace: they would be charged as a page and sent
    # to the model as blank text.
    pieces = (text[i : i + max_chars] for i in range(0, len(text), max_chars))
    return [piece.strip() for piece in pieces if piece.strip()]


def _no_constants(name: str) -> float:
    raise ValueError(f"non-standard JSON constant {name}")


def _clock(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"
