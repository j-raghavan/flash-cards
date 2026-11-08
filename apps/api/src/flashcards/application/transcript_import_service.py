# SPDX-License-Identifier: AGPL-3.0-only
"""Generate a deck from a YouTube link or a pasted transcript (F-AI-2).

Retrieval is best-effort behind TranscriptProvider: when a video has no
retrievable transcript the caller gets TranscriptUnavailable and can paste the
transcript instead. Both paths feed the same async generation job.
"""

from __future__ import annotations

from flashcards.application.deck_service import DeckService
from flashcards.application.generation_request_service import GenerationRequestService
from flashcards.domain.ai import Thoroughness
from flashcards.domain.jobs import AiJob
from flashcards.domain.ports import TranscriptProvider
from flashcards.domain.transcripts import parse_youtube_video_id, pasted_transcript


class TranscriptImportService:
    def __init__(
        self,
        decks: DeckService,
        generation: GenerationRequestService,
        transcripts: TranscriptProvider,
    ) -> None:
        self._decks = decks
        self._generation = generation
        self._transcripts = transcripts

    async def from_youtube(
        self, *, owner_id: str, deck_id: str, url: str, thoroughness: Thoroughness
    ) -> AiJob:
        video_id = parse_youtube_video_id(url)  # raises InvalidVideoUrl
        # Check the deck before any fetch, so a bad deck never costs a network call.
        await self._decks.get(owner_id=owner_id, deck_id=deck_id)
        transcript = await self._transcripts.fetch(video_id)  # raises TranscriptUnavailable
        return await self._generation.start_transcript(
            owner_id=owner_id, deck_id=deck_id, transcript=transcript, thoroughness=thoroughness
        )

    async def from_text(
        self, *, owner_id: str, deck_id: str, text: str, thoroughness: Thoroughness
    ) -> AiJob:
        return await self._generation.start_transcript(
            owner_id=owner_id,
            deck_id=deck_id,
            transcript=pasted_transcript(text),  # raises InvalidTranscript
            thoroughness=thoroughness,
        )
