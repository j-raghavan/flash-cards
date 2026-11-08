# SPDX-License-Identifier: AGPL-3.0-only
"""Transcript adapters (F-AI-2).

NullTranscriptProvider is the default: it reports every video as having no
retrievable transcript, so the YouTube route fails cleanly and points the user
at the manual paste. A real fetcher is one more TranscriptProvider adapter.

TranscriptAwareExtractor decorates the document extractor so a stored transcript
envelope is split into time-windowed text pages, letting the existing generation
worker process it unchanged. Anything else goes to the wrapped extractor.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from flashcards.domain.ports import DocumentExtractor
from flashcards.domain.sources import ExtractedPage, PageKind, SourceRef, TooManyPages
from flashcards.domain.transcripts import (
    TRANSCRIPT_MEDIA_TYPE,
    Transcript,
    TranscriptUnavailable,
    chunk_page_text,
    chunk_transcript,
    decode_transcript,
    is_transcript_envelope,
)


class NullTranscriptProvider:
    async def fetch(self, video_id: str) -> Transcript:
        raise TranscriptUnavailable("automatic transcript retrieval is not configured")


class TranscriptAwareExtractor:
    def __init__(self, inner: DocumentExtractor) -> None:
        self._inner = inner

    def sniff(self, data: bytes) -> str:
        if is_transcript_envelope(data):
            decode_transcript(data)  # validate; raises InvalidTranscript
            return TRANSCRIPT_MEDIA_TYPE
        return self._inner.sniff(data)

    def count_pages(self, data: bytes) -> int:
        if is_transcript_envelope(data):
            return len(chunk_transcript(decode_transcript(data)))
        return self._inner.count_pages(data)

    async def extract(
        self, data: bytes, *, file_id: str, max_pages: int
    ) -> AsyncIterator[ExtractedPage]:
        if not is_transcript_envelope(data):
            async for page in self._inner.extract(data, file_id=file_id, max_pages=max_pages):
                yield page
            return
        transcript = decode_transcript(data)
        chunks = chunk_transcript(transcript)
        if len(chunks) > max_pages:
            raise TooManyPages(f"{len(chunks)} transcript chunks (max {max_pages})")
        for chunk in chunks:
            yield ExtractedPage(
                ref=SourceRef(file_id=file_id, page=chunk.index),
                kind=PageKind.TEXT,
                text=chunk_page_text(transcript, chunk),
            )
