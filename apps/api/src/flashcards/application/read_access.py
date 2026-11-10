# SPDX-License-Identifier: AGPL-3.0-only
"""Read access to study material, the shared library included (ADR 0024).

This is the one place the readable rule lives: a caller may read what they own
and what any active admin owns. Writes never come through here; they keep the
owner checks of their own services, so a library deck stays read-only to
everyone but its owner.

Nothing is cached: the active admins are read on every call, so demoting,
suspending or deleting an admin takes their decks out of the library on the
next request. Anything missing or unreadable raises the usual NotFound error,
so callers cannot tell the two apart (NF-SEC-5).
"""

from __future__ import annotations

from flashcards.domain.access import ReadScope
from flashcards.domain.decks import Deck, DeckNotFound
from flashcards.domain.media import MediaAsset, MediaNotFound
from flashcards.domain.note_types import CardTemplate, CustomNoteType, NoteTypeNotFound
from flashcards.domain.notes import Card, CardNotFound, Note, NoteNotFound
from flashcards.domain.ports import (
    CardRepository,
    DeckRepository,
    MediaRepository,
    NoteRepository,
    NoteTypeRepository,
    UserRepository,
)


class ReadAccess:
    def __init__(
        self,
        users: UserRepository,
        decks: DeckRepository,
        notes: NoteRepository,
        cards: CardRepository,
        note_types: NoteTypeRepository,
        media: MediaRepository,
    ) -> None:
        self._users = users
        self._decks = decks
        self._notes = notes
        self._cards = cards
        self._note_types = note_types
        self._media = media

    async def scope(self, user_id: str) -> ReadScope:
        """The owners the caller may read right now: themselves and the active admins."""
        admins = await self._users.list_active_admin_ids()
        return ReadScope(user_id=user_id, library_owner_ids=frozenset(admins))

    async def decks(self, user_id: str) -> list[Deck]:
        """The caller's decks and the library decks, oldest first."""
        scope = await self.scope(user_id)
        return await self._decks.list_for_owners(scope.owner_ids)

    async def deck(self, user_id: str, deck_id: str) -> Deck:
        return await self._deck_in(await self.scope(user_id), deck_id)

    async def deck_cards(self, user_id: str, deck_id: str) -> list[Card]:
        """A readable deck's cards, oldest first; raises DeckNotFound."""
        deck = await self.deck(user_id, deck_id)
        return await self._cards.list_for_deck(deck.id)

    async def card(self, user_id: str, card_id: str) -> Card:
        return await self._card_in(await self.scope(user_id), card_id)

    async def study_card(self, user_id: str, card_id: str) -> Card:
        """A readable card, kept from deletion until the transaction ends, for a
        review about to write rows that reference it; raises CardNotFound."""
        scope = await self.scope(user_id)
        card = await self._cards.share_lock(card_id)
        if card is None or not scope.can_read(card.owner_id):
            raise CardNotFound(card_id)
        return card

    async def note(self, user_id: str, note_id: str) -> Note:
        return await self._note_in(await self.scope(user_id), note_id)

    async def note_type(self, user_id: str, note_type_id: str) -> CustomNoteType:
        return await self._note_type_in(await self.scope(user_id), note_type_id)

    async def media(self, user_id: str, media_id: str) -> MediaAsset:
        scope = await self.scope(user_id)
        asset = await self._media.get(media_id)
        if asset is None or not scope.can_read(asset.owner_id):
            raise MediaNotFound(media_id)
        return asset

    async def render_card(
        self, user_id: str, card_id: str
    ) -> tuple[Card, Note, CardTemplate | None]:
        """A readable card with its note and, for a custom note, its template."""
        scope = await self.scope(user_id)
        card = await self._card_in(scope, card_id)
        note = await self._note_in(scope, card.note_id)
        if note.note_type_id is None:
            return card, note, None
        note_type = await self._note_type_in(scope, note.note_type_id)
        return card, note, note_type.template(card.ordinal)

    async def _deck_in(self, scope: ReadScope, deck_id: str) -> Deck:
        deck = await self._decks.get(deck_id)
        if deck is None or not scope.can_read(deck.owner_id):
            raise DeckNotFound(deck_id)
        return deck

    async def _card_in(self, scope: ReadScope, card_id: str) -> Card:
        card = await self._cards.get(card_id)
        if card is None or not scope.can_read(card.owner_id):
            raise CardNotFound(card_id)
        return card

    async def _note_in(self, scope: ReadScope, note_id: str) -> Note:
        note = await self._notes.get(note_id)
        if note is None or not scope.can_read(note.owner_id):
            raise NoteNotFound(note_id)
        return note

    async def _note_type_in(self, scope: ReadScope, note_type_id: str) -> CustomNoteType:
        note_type = await self._note_types.get(note_type_id)
        if note_type is None or not scope.can_read(note_type.owner_id):
            raise NoteTypeNotFound(note_type_id)
        return note_type
