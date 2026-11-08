# SPDX-License-Identifier: AGPL-3.0-only
"""Deck-summary use case (F-AI-6).

A summary unlocks only once the deck has enough cards. The deck's material is
sent to the model as a data document, never concatenated into the instructions
(NF-SEC-6), and bounded in size to cap token cost.
"""

from __future__ import annotations

from flashcards.application.ai_prompts import summary_prompt
from flashcards.application.deck_service import DeckService
from flashcards.domain.ai import LlmDocument, StructuredRequest
from flashcards.domain.notes import Note
from flashcards.domain.ports import CardRepository, LLMProvider, NoteRepository
from flashcards.domain.summary import (
    MAX_SUMMARY_SOURCE_CHARS,
    SUMMARY_MAX_OUTPUT_TOKENS,
    SUMMARY_SCHEMA,
    DeckSummary,
    SummaryLocked,
    parse_summary,
)


class SummaryService:
    def __init__(
        self,
        decks: DeckService,
        notes: NoteRepository,
        cards: CardRepository,
        llm: LLMProvider,
        *,
        unlock_cards: int,
    ) -> None:
        self._decks = decks
        self._notes = notes
        self._cards = cards
        self._llm = llm
        self._unlock_cards = unlock_cards

    async def summarize(self, *, owner_id: str, deck_id: str) -> DeckSummary:
        # The deck counts with its subdecks. Raises DeckNotFound if the deck is
        # missing or not the caller's.
        deck_ids = await self._decks.subtree_ids(owner_id=owner_id, deck_id=deck_id)
        card_count = sum([len(await self._cards.list_for_deck(d)) for d in deck_ids])
        if card_count < self._unlock_cards:
            raise SummaryLocked(required=self._unlock_cards, have=card_count)
        notes = [n for d in deck_ids for n in await self._notes.list_for_deck(d)]
        source = _collect_source(notes)
        request = StructuredRequest(
            instructions=summary_prompt(),
            documents=(LlmDocument(media_type="text/plain", data=source.encode("utf-8")),),
            schema=SUMMARY_SCHEMA,
            max_output_tokens=SUMMARY_MAX_OUTPUT_TOKENS,
        )
        result = await self._llm.generate_structured(request)
        return parse_summary(result.data)


def _collect_source(notes: list[Note]) -> str:
    lines: list[str] = []
    total = 0
    for note in notes:
        for name, value in note.fields:
            line = f"{name}: {value}"
            total += len(line) + 1
            if total > MAX_SUMMARY_SOURCE_CHARS:
                return "\n".join(lines)
            lines.append(line)
    return "\n".join(lines)
