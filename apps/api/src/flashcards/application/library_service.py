# SPDX-License-Identifier: AGPL-3.0-only
"""Cross-aggregate deletion: removing a deck or note and everything under it.

Deck and note deletion spans the Cards and Scheduling contexts, so it lives in a
coordinator rather than any single aggregate's service. Both the in-memory and
SQL backends go through this same path, so they delete identically (the SQL FK
cascades are a database-integrity backstop, not the primary mechanism).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.domain.note_types import CustomNoteType
from flashcards.domain.notes import (
    Card,
    InvalidNote,
    Note,
    Occlusion,
    card_ordinals,
    note_fields,
)
from flashcards.domain.ports import (
    CardRepository,
    DeckRepository,
    MockExamRepository,
    NoteRepository,
    PassageRepository,
    ReviewLogRepository,
    ScheduleRepository,
    ShareLinkRepository,
)
from flashcards.domain.tags import normalize_tags


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
        passage_repo: PassageRepository,
        mock_exam_repo: MockExamRepository,
        share_link_repo: ShareLinkRepository,
    ) -> None:
        self._decks = decks
        self._notes = notes
        self._deck_repo = deck_repo
        self._note_repo = note_repo
        self._card_repo = card_repo
        self._schedule_repo = schedule_repo
        self._review_log_repo = review_log_repo
        self._passage_repo = passage_repo
        self._mock_exam_repo = mock_exam_repo
        self._share_link_repo = share_link_repo

    async def delete_deck(self, *, owner_id: str, deck_id: str) -> None:
        """Delete a deck subtree and everything in it.

        That covers notes, cards, schedules and logs, plus the chat passages
        extracted from its sources, its mock exams (which hold reference answers) and
        its share links.
        """
        # Raises DeckNotFound if the deck is missing or not the caller's.
        subtree = await self._decks.subtree_ids(owner_id=owner_id, deck_id=deck_id)
        for sub_deck_id in subtree:
            for card in await self._card_repo.list_for_deck(sub_deck_id):
                await self._purge_card(card.id)
            await self._card_repo.delete_for_deck(sub_deck_id)
            await self._note_repo.delete_for_deck(sub_deck_id)
            await self._passage_repo.delete_for_deck(sub_deck_id)
            await self._mock_exam_repo.delete_for_deck(sub_deck_id)
            await self._share_link_repo.delete_for_deck(sub_deck_id)
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

    async def update_note(
        self,
        *,
        owner_id: str,
        note_id: str,
        fields: Mapping[str, str] | None = None,
        tags: Iterable[str] | None = None,
        custom: CustomNoteType | None = None,
        occlusion: Occlusion | None = None,
    ) -> tuple[Note, list[Card]]:
        """Edit a note's fields, tags or masks (ADR 0011, 0013); returns it and its cards.

        New fields or masks re-derive the cards: a card whose ordinal survives
        keeps its schedule and history, a removed cloze deletion, template or mask
        takes its card, schedule and history with it, and a new one gets a new
        card. Everything is checked before anything is written. The note is locked
        first, so concurrent edits or moves of it apply one after the other.

        A custom note passes its type (locked by the caller), and new masks come
        with an image the caller has checked is the owner's.
        """
        note = await self._notes.lock_note(owner_id=owner_id, note_id=note_id)
        if custom is not None and custom.id != note.note_type_id:
            raise InvalidNote("the note type given is not this note's type")
        updated = note
        if tags is not None:
            updated = updated.with_tags(normalize_tags(tags))
        if fields is not None:
            updated = updated.with_fields(note_fields(note.note_type, fields, custom))
        if occlusion is not None:
            updated = updated.with_occlusion(occlusion)
        wanted = card_ordinals(  # raises InvalidNote
            updated.note_type, updated.fields, custom=custom, occlusion=updated.occlusion
        )
        return updated, await self.rederive(updated, wanted)

    async def rederive(self, updated: Note, wanted: list[int]) -> list[Card]:
        """Save a changed note and match its cards to `wanted`; returns its cards.

        The note must be locked and `wanted` validated. Surviving ordinals keep
        their card, schedule and history; the rest are purged; new ones are added.
        """
        cards = await self._notes.cards_of(updated)
        await self._note_repo.update(updated)
        for card in cards:
            if card.ordinal not in wanted:
                await self._purge_card(card.id)
                await self._card_repo.delete(card.id)
        have = {card.ordinal for card in cards}
        await self._notes.add_cards(updated, [o for o in wanted if o not in have])
        return await self._notes.cards_of(updated)

    async def delete_note(self, *, owner_id: str, note_id: str) -> None:
        """Delete a note and the cards, schedules and logs it produced."""
        # Raises NoteNotFound if the note is missing or not the caller's.
        await self._notes.get_note(owner_id=owner_id, note_id=note_id)
        for card in await self._card_repo.list_for_notes([note_id]):
            await self._purge_card(card.id)
        await self._card_repo.delete_for_note(note_id)
        await self._note_repo.delete(note_id)

    async def _purge_card(self, card_id: str) -> None:
        await self._schedule_repo.delete_for_card(card_id)
        await self._review_log_repo.delete_for_card(card_id)
