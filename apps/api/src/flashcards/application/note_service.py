# SPDX-License-Identifier: AGPL-3.0-only
"""Use cases for notes and the cards they produce (F-CARD-1..4, F-CARD-6)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping

from flashcards.application.deck_service import DeckService
from flashcards.domain.note_types import CustomNoteType, NoteTypeNotFound
from flashcards.domain.notes import (
    Card,
    CardNotFound,
    Fields,
    Note,
    NoteNotFound,
    NoteType,
    Occlusion,
    card_ordinals,
    note_fields,
)
from flashcards.domain.ports import CardRepository, Clock, NoteRepository
from flashcards.domain.tags import normalize_tags


class NoteService:
    def __init__(
        self,
        decks: DeckService,
        notes: NoteRepository,
        cards: CardRepository,
        clock: Clock,
    ) -> None:
        self._decks = decks
        self._notes = notes
        self._cards = cards
        self._clock = clock

    async def create_note(
        self,
        *,
        owner_id: str,
        deck_id: str,
        note_type: NoteType,
        fields: Mapping[str, str],
        tags: Iterable[str] = (),
        custom: CustomNoteType | None = None,
        occlusion: Occlusion | None = None,
    ) -> tuple[Note, list[Card]]:
        """Create a note and its cards; raises InvalidNote or InvalidTags.

        A custom note passes its type and an image occlusion note its masks. The
        caller has checked that an occlusion image is the owner's own
        (NoteTypeService.create_note does).
        """
        normalized_tags = normalize_tags(tags)
        # Raises DeckNotFound if the deck is missing or not the caller's.
        await self._decks.get(owner_id=owner_id, deck_id=deck_id)
        if custom is not None and custom.owner_id != owner_id:
            raise NoteTypeNotFound(custom.id)
        field_pairs: Fields = note_fields(note_type, fields, custom)
        ordinals = card_ordinals(note_type, field_pairs, custom=custom, occlusion=occlusion)

        now = self._clock.now()
        note = Note(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            deck_id=deck_id,
            note_type=note_type,
            fields=field_pairs,
            created_at=now,
            tags=normalized_tags,
            note_type_id=custom.id if custom is not None else None,
            occlusion=occlusion,
        )
        await self._notes.add(note)
        return note, await self.add_cards(note, ordinals)

    async def add_cards(self, note: Note, ordinals: list[int]) -> list[Card]:
        """Create the note's cards for these ordinals, in the note's deck."""
        now = self._clock.now()
        cards = [
            Card(
                id=str(uuid.uuid4()),
                owner_id=note.owner_id,
                deck_id=note.deck_id,
                note_id=note.id,
                ordinal=ordinal,
                created_at=now,
            )
            for ordinal in ordinals
        ]
        for card in cards:
            await self._cards.add(card)
        return cards

    async def cards_of(self, note: Note) -> list[Card]:
        """The note's cards, by ordinal."""
        return await self._cards.list_for_notes([note.id])

    async def lock_note(self, *, owner_id: str, note_id: str) -> Note:
        """The caller's note, locked against concurrent changes; raises NoteNotFound."""
        locked = await self._notes.lock_many([note_id])
        if not locked or locked[0].owner_id != owner_id:
            raise NoteNotFound(note_id)
        return locked[0]

    async def list_cards(self, *, owner_id: str, deck_id: str) -> list[Card]:
        await self._decks.get(owner_id=owner_id, deck_id=deck_id)
        return await self._cards.list_for_deck(deck_id)

    async def get_card(self, *, owner_id: str, card_id: str) -> Card:
        card = await self._cards.get(card_id)
        if card is None or card.owner_id != owner_id:
            raise CardNotFound(card_id)
        return card

    async def get_note(self, *, owner_id: str, note_id: str) -> Note:
        note = await self._notes.get(note_id)
        if note is None or note.owner_id != owner_id:
            raise NoteNotFound(note_id)
        return note

    async def suspend_card(self, *, owner_id: str, card_id: str) -> Card:
        card = await self.get_card(owner_id=owner_id, card_id=card_id)
        suspended = card.suspended(self._clock.now())
        await self._cards.update(suspended)
        return suspended

    async def unsuspend_card(self, *, owner_id: str, card_id: str) -> Card:
        card = await self.get_card(owner_id=owner_id, card_id=card_id)
        restored = card.unsuspended()
        await self._cards.update(restored)
        return restored

    async def delete_note(self, *, owner_id: str, note_id: str) -> None:
        note = await self._notes.get(note_id)
        if note is None or note.owner_id != owner_id:
            raise NoteNotFound(note_id)
        await self._cards.delete_for_note(note_id)
        await self._notes.delete(note_id)
