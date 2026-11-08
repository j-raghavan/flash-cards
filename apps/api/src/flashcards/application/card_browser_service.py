# SPDX-License-Identifier: AGPL-3.0-only
"""The card browser: search, filter, sort and page a user's cards (F-DECK-2, ADR 0011).

Loads the owner's cards, notes and schedules in three batched reads and filters
in memory, so both storage modes behave identically.
"""

from __future__ import annotations

from dataclasses import replace

from flashcards.application.deck_service import DeckService
from flashcards.domain.browser import (
    BrowserPage,
    BrowserQuery,
    BrowserRow,
    card_proficiency,
    card_state,
    matches,
    preview,
    sort_key,
)
from flashcards.domain.ports import CardRepository, Clock, NoteRepository, ScheduleRepository


class CardBrowserService:
    def __init__(
        self,
        decks: DeckService,
        notes: NoteRepository,
        cards: CardRepository,
        schedules: ScheduleRepository,
        clock: Clock,
    ) -> None:
        self._decks = decks
        self._notes = notes
        self._cards = cards
        self._schedules = schedules
        self._clock = clock

    async def search(
        self,
        *,
        owner_id: str,
        query: BrowserQuery,
        deck_id: str | None = None,
        include_subdecks: bool = True,
    ) -> BrowserPage:
        """Raises DeckNotFound when `deck_id` is missing or not the caller's."""
        if deck_id is not None:
            scope = (
                await self._decks.subtree_ids(owner_id=owner_id, deck_id=deck_id)
                if include_subdecks
                else [(await self._decks.get(owner_id=owner_id, deck_id=deck_id)).id]
            )
            query = replace(query, deck_ids=frozenset(scope))
        now = self._clock.now()
        deck_names = {deck.id: deck.name for deck in await self._decks.list_decks(owner_id)}
        notes = {note.id: note for note in await self._notes.list_for_owner(owner_id)}
        cards = await self._cards.list_for_owner(owner_id)
        schedules = await self._schedules.get_many([card.id for card in cards])

        found: list[BrowserRow] = []
        for card in cards:
            note = notes.get(card.note_id)
            if note is None:  # pragma: no cover - cards are deleted with their note
                continue
            schedule = schedules.get(card.id)
            row = BrowserRow(
                card_id=card.id,
                note_id=note.id,
                deck_id=card.deck_id,
                deck_name=deck_names.get(card.deck_id, ""),
                ordinal=card.ordinal,
                note_type=note.note_type,
                preview=preview(note),
                tags=note.tags,
                flag=card.flag,
                state=card_state(card, schedule, now),
                due_at=schedule.due_at if schedule is not None else None,
                proficiency=card_proficiency(schedule, now),
                created_at=card.created_at,
            )
            if matches(row, note, query):
                found.append(row)
        found.sort(key=lambda row: sort_key(row, query.sort), reverse=query.descending)
        page = found[query.offset : query.offset + query.limit]
        return BrowserPage(rows=tuple(page), total=len(found))
