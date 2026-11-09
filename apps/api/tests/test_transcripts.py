# SPDX-License-Identifier: AGPL-3.0-only
"""Transcript parsing, envelope, chunking and the extractor decorator (F-AI-2)."""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator

import pytest

from flashcards.adapters.transcripts import NullTranscriptProvider, TranscriptAwareExtractor
from flashcards.domain.sources import ExtractedPage, PageKind, SourceRef, TooManyPages
from flashcards.domain.transcripts import (
    ENVELOPE_PREFIX,
    MAX_ENVELOPE_BYTES,
    MAX_SEGMENTS,
    MAX_TRANSCRIPT_CHARS,
    TRANSCRIPT_MEDIA_TYPE,
    InvalidTranscript,
    InvalidVideoUrl,
    Transcript,
    TranscriptSegment,
    TranscriptUnavailable,
    chunk_page_text,
    chunk_transcript,
    decode_transcript,
    encode_transcript,
    parse_language_preference,
    parse_youtube_video_id,
    pasted_transcript,
    validate_video_id,
)

VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}",
        f"https://youtube.com/watch?v={VID}&t=42s",
        f"https://m.youtube.com/watch?v={VID}",
        f"http://youtu.be/{VID}",
        f"https://youtu.be/{VID}?si=abc",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://www.youtube.com/embed/{VID}",
        f"https://www.youtube.com/live/{VID}",
        f"  https://www.youtube.com/watch?v={VID}  ",
        f"https://www.youtube.com/watch/?v={VID}",
        f"https://www.youtube-nocookie.com/embed/{VID}",
        f"https://youtube.com/v/{VID}",
    ],
)
def test_parse_youtube_video_id_accepts_known_forms(url: str) -> None:
    assert parse_youtube_video_id(url) == VID


@pytest.mark.parametrize(
    "url",
    [
        f"ftp://youtube.com/watch?v={VID}",  # not http(s)
        f"https://evil.example/watch?v={VID}",  # not YouTube
        "https://www.youtube.com/watch?v=short",  # bad id
        "https://www.youtube.com/watch",  # no id
        "https://www.youtube.com/channel/abc",  # not a video path
        "https://youtu.be/",  # empty id
        "not a url",
        f"https://www.youtube.com/watch?v={VID}%0A",  # a trailing newline on the id
        f"https://youtu.be/{VID}%0A",
        f"https://www.youtube.com/watch?v={VID}x",  # twelve characters
    ],
)
def test_parse_youtube_video_id_rejects_others(url: str) -> None:
    with pytest.raises(InvalidVideoUrl):
        parse_youtube_video_id(url)


def test_pasted_transcript_is_one_untimed_segment() -> None:
    assert pasted_transcript("  hello  ") == Transcript(
        video_id=None, segments=(TranscriptSegment(0.0, "hello"),)
    )
    with pytest.raises(InvalidTranscript):
        pasted_transcript("   ")


def _video(*texts: str) -> Transcript:
    return Transcript(
        video_id=VID,
        segments=tuple(TranscriptSegment(start=float(i * 30), text=t) for i, t in enumerate(texts)),
    )


def test_envelope_round_trips() -> None:
    transcript = _video("first", "second")
    data = encode_transcript(transcript)
    assert data.startswith(ENVELOPE_PREFIX)
    assert decode_transcript(data) == transcript


def test_encode_rejects_non_finite_provider_timestamps() -> None:
    with pytest.raises(InvalidTranscript):
        encode_transcript(Transcript(video_id=VID, segments=(TranscriptSegment(math.nan, "a"),)))


def test_encode_rejects_empty_and_too_long() -> None:
    with pytest.raises(InvalidTranscript):
        encode_transcript(Transcript(video_id=None, segments=(TranscriptSegment(0.0, " "),)))
    with pytest.raises(InvalidTranscript):
        encode_transcript(Transcript(video_id=None, segments=()))
    too_long = "x" * (MAX_TRANSCRIPT_CHARS + 1)
    with pytest.raises(InvalidTranscript):
        encode_transcript(Transcript(video_id=None, segments=(TranscriptSegment(0.0, too_long),)))


def _envelope(body: object) -> bytes:
    return ENVELOPE_PREFIX + json.dumps(body).encode("utf-8")


@pytest.mark.parametrize(
    "data",
    [
        b"%PDF-1.4 not an envelope",
        ENVELOPE_PREFIX + b"{not json",
        ENVELOPE_PREFIX + b"\xff\xfe",
        _envelope([1, 2]),
        _envelope({"video_id": 5, "segments": []}),
        _envelope({"video_id": None, "segments": "nope"}),
        _envelope({"video_id": None, "segments": [[0, "a", "extra"]]}),
        _envelope({"video_id": None, "segments": ["flat"]}),
        _envelope({"video_id": None, "segments": [["0", "a"]]}),
        _envelope({"video_id": None, "segments": [[True, "a"]]}),
        _envelope({"video_id": None, "segments": [[-1, "a"]]}),
        _envelope({"video_id": None, "segments": [[0, 7]]}),
        _envelope({"video_id": None, "segments": [[0, "   "]]}),
        ENVELOPE_PREFIX + b'{"video_id": null, "segments": [[NaN, "a"]]}',
        ENVELOPE_PREFIX + b'{"video_id": null, "segments": [[Infinity, "a"]]}',
        _envelope({"video_id": None, "segments": [[1e12, "a"]]}),
        ENVELOPE_PREFIX + b"[" * 100_000,
        ENVELOPE_PREFIX + b"x" * MAX_ENVELOPE_BYTES,
        _envelope({"video_id": None, "segments": [[0, "a"]] * (MAX_SEGMENTS + 1)}),
    ],
)
def test_decode_rejects_malformed(data: bytes) -> None:
    with pytest.raises(InvalidTranscript):
        decode_transcript(data)


def test_chunking_groups_segments_and_splits_long_ones() -> None:
    transcript = _video("aaaa", "bbbb", "cccc", "d" * 12, "", "ee")
    chunks = chunk_transcript(transcript, max_chars=9)
    # "aaaa bbbb" fits (9); "cccc" starts a new chunk; the 12-char segment is split.
    assert [c.text for c in chunks] == ["aaaa bbbb", "cccc", "ddddddddd", "ddd ee"]
    assert [c.index for c in chunks] == [1, 2, 3, 4]
    # A chunk ends where the segment after its last one starts.
    assert (chunks[0].start, chunks[0].end) == (0.0, 60.0)
    assert (chunks[3].start, chunks[3].end) == (90.0, 150.0)


def test_chunking_skips_whitespace_only_pieces() -> None:
    transcript = Transcript(video_id=None, segments=(TranscriptSegment(0.0, "a" + " " * 20 + "b"),))
    # The middle 9-char window is all spaces: it must not become a blank chunk, and
    # the trimmed pieces around it pack together.
    assert [c.text for c in chunk_transcript(transcript, max_chars=9)] == ["a b"]


def test_chunking_an_empty_transcript_yields_no_chunks() -> None:
    assert chunk_transcript(Transcript(video_id=None, segments=())) == []
    assert chunk_transcript(_video("", "  ")) == []


def test_chunk_page_text_adds_a_time_range_only_for_videos() -> None:
    video = _video("x" * 5, "y")
    chunk = chunk_transcript(video, max_chars=100)[0]
    assert chunk_page_text(video, chunk) == "[00:00:00 - 00:00:30]\nxxxxx y"
    pasted = pasted_transcript("plain text")
    assert chunk_page_text(pasted, chunk_transcript(pasted)[0]) == "plain text"


def test_clock_formats_hours() -> None:
    transcript = Transcript(
        video_id=VID,
        segments=(TranscriptSegment(3725.0, "a"), TranscriptSegment(3726.9, "b")),
    )
    chunk = chunk_transcript(transcript)[0]
    assert chunk_page_text(transcript, chunk).startswith("[01:02:05 - 01:02:06]")


async def test_null_provider_reports_no_transcript() -> None:
    with pytest.raises(TranscriptUnavailable):
        await NullTranscriptProvider().fetch(VID)


class _Inner:
    def sniff(self, data: bytes) -> str:
        return "application/pdf"

    async def count_pages(self, data: bytes) -> int:
        return 3

    async def extract(
        self, data: bytes, *, file_id: str, max_pages: int
    ) -> AsyncIterator[ExtractedPage]:
        yield ExtractedPage(ref=SourceRef(file_id, 1), kind=PageKind.TEXT, text="inner")


async def test_extractor_decorator_delegates_non_transcripts() -> None:
    extractor = TranscriptAwareExtractor(_Inner())
    assert extractor.sniff(b"%PDF") == "application/pdf"
    assert await extractor.count_pages(b"%PDF") == 3
    pages = [p async for p in extractor.extract(b"%PDF", file_id="f", max_pages=5)]
    assert [p.text for p in pages] == ["inner"]


async def test_extractor_decorator_splits_a_transcript_into_text_pages() -> None:
    extractor = TranscriptAwareExtractor(_Inner())
    data = encode_transcript(_video("hello", "world"))
    assert extractor.sniff(data) == TRANSCRIPT_MEDIA_TYPE
    assert await extractor.count_pages(data) == 1
    pages = [p async for p in extractor.extract(data, file_id="src", max_pages=5)]
    assert len(pages) == 1
    assert pages[0].ref == SourceRef("src", 1) and pages[0].kind is PageKind.TEXT
    assert pages[0].text == "[00:00:00 - 00:00:30]\nhello world"


async def test_extractor_decorator_enforces_the_page_cap() -> None:
    extractor = TranscriptAwareExtractor(_Inner())
    data = encode_transcript(_video("a" * 7000))  # two chunks at the default size
    with pytest.raises(TooManyPages):
        _ = [p async for p in extractor.extract(data, file_id="src", max_pages=1)]


def test_extractor_decorator_rejects_a_malformed_envelope() -> None:
    with pytest.raises(InvalidTranscript):
        TranscriptAwareExtractor(_Inner()).sniff(ENVELOPE_PREFIX + b"junk")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("en", ("en",)), (" de , en ", ("de", "en")), ("pt-BR,zh-Hant-TW", ("pt-BR", "zh-Hant-TW"))],
)
def test_language_preference_parses_tags_in_order(value: str, expected: tuple[str, ...]) -> None:
    assert parse_language_preference(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", " , ", "english", "en;de", "e", "e n", ",".join(["en"] * 11), "en-"],
)
def test_language_preference_rejects_bad_tags(value: str) -> None:
    with pytest.raises(ValueError):
        parse_language_preference(value)


def test_validate_video_id_is_exact() -> None:
    assert validate_video_id(VID) == VID
    for bad in (f"{VID}\n", f" {VID}", "a/b/c/d/e/f", ""):
        with pytest.raises(InvalidVideoUrl):
            validate_video_id(bad)
