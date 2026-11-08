# SPDX-License-Identifier: AGPL-3.0-only
"""Bulk actions over cards selected in the browser (F-DECK-3, ADR 0011).

Move, delete and tag act on the notes of the selected cards, so a note's cards
always stay together; suspend, bury and flag act on the cards themselves. Every
id and argument is checked before anything is written, so a request either
applies in full or changes nothing.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from flashcards.application.deck_service import DeckService
from flashcards.application.library_service import LibraryService
from flashcards.domain.notes import Card, CardNotFound, Note
from flashcards.domain.ports import CardRepository, Clock, NoteRepository
from flashcards.domain.tags import merge_tags, normalize_tags, remove_tags

MAX_BULK_CARDS = 1_000


class BulkAction(enum.StrEnum):
    MOVE = "move"
    DELETE = "delete"
    ADD_TAGS = "add_tags"
    REMOVE_TAGS = "remove_tags"
    SUSPEND = "suspend"
    UNSUSPEND = "unsuspend"
    BURY = "bury"
    UNBURY = "unbury"
    FLAG = "flag"


_NOTE_ACTIONS = frozenset(
    {BulkAction.MOVE, BulkAction.DELETE, BulkAction.ADD_TAGS, BulkAction.REMOVE_TAGS}
)


@dataclass(frozen=True, slots=True)
class BulkResult:
    cards: int  # cards changed or removed (already in the requested state: not counted)
    notes: int  # notes changed or removed (0 for card-level actions)


class BulkCardService:
    def __init__(
        self,
        decks: DeckService,
        library: LibraryService,
        notes: NoteRepository,
        cards: CardRepository,
        clock: Clock,
    ) -> None:
        self._decks = decks
        self._library = library
        self._notes = notes
        self._cards = cards
        self._clock = clock

    async def apply(
        self,
        *,
        owner_id: str,
        card_ids: Sequence[str],
        action: BulkAction,
        deck_id: str | None = None,
        tags: Iterable[str] = (),
        flag: int = 0,
        until: datetime | None = None,
    ) -> BulkResult:
        """Raises CardNotFound (any id not the caller's), DeckNotFound, or a 422 error."""
        unique_ids = list(dict.fromkeys(card_ids))
        owned = {
            card.id: card
            for card in await self._cards.get_many(unique_ids)
            if card.owner_id == owner_id
        }
        missing = [card_id for card_id in unique_ids if card_id not in owned]
        if missing:
            raise CardNotFound(missing[0])  # missing and other users' cards look the same
        selected = [owned[card_id] for card_id in unique_ids]
        now = self._clock.now()

        if action in _NOTE_ACTIONS:
            # Locked in id order, so concurrent edits and moves of a note apply in turn.
            notes = await self._notes.lock_many([card.note_id for card in selected])
            cards_by_note: dict[str, list[Card]] = {}
            for card in await self._cards.list_for_notes([note.id for note in notes]):
                cards_by_note.setdefault(card.note_id, []).append(card)
            affected = sum(len(cards) for cards in cards_by_note.values())
            await self._apply_to_notes(
                owner_id, action, notes, cards_by_note, deck_id=deck_id, tags=tags
            )
            return BulkResult(cards=affected, notes=len(notes))

        # Every change is computed (and validated) before the first write; only cards
        # that actually change are written and counted.
        changes = [
            (card, _card_change(card, action, now=now, flag=flag, until=until))
            for card in selected
        ]
        changed = [new for old, new in changes if new != old]
        for card in changed:
            await self._cards.update(card)
        return BulkResult(cards=len(changed), notes=0)

    async def _apply_to_notes(
        self,
        owner_id: str,
        action: BulkAction,
        notes: list[Note],
        cards_by_note: dict[str, list[Card]],
        *,
        deck_id: str | None,
        tags: Iterable[str],
    ) -> None:
        if action is BulkAction.DELETE:
            for note in notes:
                await self._library.delete_note(owner_id=owner_id, note_id=note.id)
            return
        if action is BulkAction.MOVE:
            if deck_id is None:
                raise ValueError("move needs a deck_id")
            await self._decks.get(owner_id=owner_id, deck_id=deck_id)  # DeckNotFound
            for note in notes:
                await self._notes.update(note.moved_to(deck_id))
                for card in cards_by_note.get(note.id, []):
                    await self._cards.update(card.moved_to(deck_id))
            return
        wanted = normalize_tags(tags)  # InvalidTags
        if action is BulkAction.ADD_TAGS:
            updated = [note.with_tags(merge_tags(note.tags, wanted)) for note in notes]
        else:
            updated = [note.with_tags(remove_tags(note.tags, wanted)) for note in notes]
        for note in updated:  # every note's limit checked before the first write
            await self._notes.update(note)


def _card_change(
    card: Card, action: BulkAction, *, now: datetime, flag: int, until: datetime | None
) -> Card:
    if action is BulkAction.SUSPEND:
        return card if card.is_suspended else card.suspended(now)
    if action is BulkAction.UNSUSPEND:
        return card.unsuspended()
    if action is BulkAction.BURY:
        return card.buried(now, until)  # InvalidBury
    if action is BulkAction.UNBURY:
        return card.unburied()
    return card.with_flag(flag)  # BulkAction.FLAG; InvalidFlag
