# SPDX-License-Identifier: AGPL-3.0-only
"""Exporting a deck and its subdecks as .apkg, CSV or JSON (F-IMP-4, ADR 0014).

Only the caller's own deck tree is read: ownership of the root is checked, the
tree is built from the caller's decks alone, and every note, card and review log
is filtered by owner as well, so an export can never carry another user's data.
"""

from __future__ import annotations

from flashcards.application.deck_service import DeckService
from flashcards.domain.deck_export import (
    MAX_EXPORT_CARDS,
    DeckExport,
    ExportedCard,
    ExportedNote,
    ExportFile,
    ExportFormat,
    ExportTooLarge,
    export_filename,
)
from flashcards.domain.notes import Card, Note
from flashcards.domain.ports import (
    CardRepository,
    Clock,
    DeckExportWriter,
    NoteRepository,
    ReviewLogRepository,
    ScheduleRepository,
)
from flashcards.domain.scheduling import ReviewLog


class DeckExportService:
    def __init__(
        self,
        decks: DeckService,
        notes: NoteRepository,
        cards: CardRepository,
        schedules: ScheduleRepository,
        logs: ReviewLogRepository,
        writer: DeckExportWriter,
        clock: Clock,
    ) -> None:
        self._decks = decks
        self._notes = notes
        self._cards = cards
        self._schedules = schedules
        self._logs = logs
        self._writer = writer
        self._clock = clock

    async def export(
        self, *, owner_id: str, deck_id: str, export_format: ExportFormat
    ) -> ExportFile:
        """The deck tree as a file; raises DeckNotFound or ExportTooLarge."""
        # Raises DeckNotFound if the deck is missing or not the caller's.
        subtree = await self._decks.subtree_ids(owner_id=owner_id, deck_id=deck_id)
        by_id = {deck.id: deck for deck in await self._decks.list_decks(owner_id)}
        paths: dict[str, tuple[str, ...]] = {}
        for tree_id in subtree:  # pre-order, so a parent's path is always known first
            deck = by_id[tree_id]
            parent = paths.get(deck.parent_id) if deck.parent_id is not None else None
            paths[tree_id] = (*parent, deck.name) if parent is not None else (deck.name,)

        notes: list[Note] = []
        cards: list[Card] = []
        for tree_id in subtree:
            deck_notes = await self._notes.list_for_deck(tree_id)
            deck_cards = await self._cards.list_for_deck(tree_id)
            notes.extend(note for note in deck_notes if note.owner_id == owner_id)
            cards.extend(card for card in deck_cards if card.owner_id == owner_id)
            if len(cards) > MAX_EXPORT_CARDS:
                raise ExportTooLarge(deck_id)

        card_ids = {card.id for card in cards}
        schedules = await self._schedules.get_many(sorted(card_ids))
        reviews: dict[str, list[ReviewLog]] = {}
        for log in await self._logs.list_for_owner(owner_id):  # oldest first
            if log.card_id in card_ids:
                reviews.setdefault(log.card_id, []).append(log)
        cards_by_note: dict[str, list[Card]] = {}
        for card in cards:
            cards_by_note.setdefault(card.note_id, []).append(card)

        carried = [note for note in notes if export_format.carries(note.note_type)]
        exported = DeckExport(
            decks=tuple(paths[tree_id] for tree_id in subtree),
            notes=tuple(
                ExportedNote(
                    note=note,
                    deck=paths[note.deck_id],
                    cards=tuple(
                        ExportedCard(
                            card=card,
                            schedule=schedules.get(card.id),
                            reviews=tuple(reviews.get(card.id, ())),
                        )
                        for card in sorted(
                            cards_by_note.get(note.id, ()), key=lambda c: c.ordinal
                        )
                    ),
                )
                for note in carried
            ),
            exported_at=self._clock.now(),
        )
        return ExportFile(
            filename=export_filename(by_id[deck_id].name, export_format),
            media_type=export_format.media_type,
            data=self._writer.write(exported, export_format),
            omitted_notes=len(notes) - len(carried),
        )
