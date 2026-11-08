# SPDX-License-Identifier: AGPL-3.0-only
"""Cross-aggregate deletion: removing a deck or note and everything under it.

Deck and note deletion spans the Cards and Scheduling contexts, so it lives in a
coordinator rather than any single aggregate's service. Both the in-memory and
SQL backends go through this same path, so they delete identically (the SQL FK
cascades are a database-integrity backstop, not the primary mechanism).
"""

from __future__ import annotations

from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.domain.ports import (
    CardRepository,
    DeckRepository,
    NoteRepository,
    ReviewLogRepository,
    ScheduleRepository,
)


class LibraryService:
    def __init__(
        self,
        decks: DeckService,
        notes: NoteService,
        deck_repo: DeckRepository,
        note_repo: NoteRepository,
        card_repo: CardRepository,
        schedule_repo: ScheduleRepository,
        review_log_repo: ReviewLogRepository,
    ) -> None:
        self._decks = decks
        self._notes = notes
        self._deck_repo = deck_repo
        self._note_repo = note_repo
        self._card_repo = card_repo
        self._schedule_repo = schedule_repo
        self._review_log_repo = review_log_repo

    async def delete_deck(self, *, owner_id: str, deck_id: str) -> None:
        """Delete a deck subtree and every note, card, schedule and log inside it."""
        # Raises DeckNotFound if the deck is missing or not the caller's.
        subtree = await self._decks.subtree_ids(owner_id=owner_id, deck_id=deck_id)
        for sub_deck_id in subtree:
            for card in await self._card_repo.list_for_deck(sub_deck_id):
                await self._purge_card(card.id)
            await self._card_repo.delete_for_deck(sub_deck_id)
            await self._note_repo.delete_for_deck(sub_deck_id)
            await self._deck_repo.delete(sub_deck_id)

    async def delete_owner_library(self, owner_id: str) -> None:
        """Delete every deck the owner has, with its notes, cards, schedules and logs.

        Flat over all owned decks rather than a root-down walk, so a deck with a
        dangling or non-rooted parent_id is still removed (account deletion, F-IMP-5).
        """
        for deck in await self._deck_repo.list_for_owner(owner_id):
            for card in await self._card_repo.list_for_deck(deck.id):
                await self._purge_card(card.id)
            await self._card_repo.delete_for_deck(deck.id)
            await self._note_repo.delete_for_deck(deck.id)
            await self._deck_repo.delete(deck.id)

    async def delete_note(self, *, owner_id: str, note_id: str) -> None:
        """Delete a note and the cards, schedules and logs it produced."""
        # Raises NoteNotFound if the note is missing or not the caller's.
        note = await self._notes.get_note(owner_id=owner_id, note_id=note_id)
        for card in await self._card_repo.list_for_deck(note.deck_id):
            if card.note_id == note_id:
                await self._purge_card(card.id)
        await self._card_repo.delete_for_note(note_id)
        await self._note_repo.delete(note_id)

    async def _purge_card(self, card_id: str) -> None:
        await self._schedule_repo.delete(card_id)
        await self._review_log_repo.delete_for_card(card_id)
