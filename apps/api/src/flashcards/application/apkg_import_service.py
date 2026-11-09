# SPDX-License-Identifier: AGPL-3.0-only
"""Importing a parsed Anki package into decks and cards (F-IMP-1).

Each Anki deck that holds cards becomes one of the caller's decks; each note is
mapped to Basic or Cloze and created there. Notes whose type does not map, or
whose fields do not pass our validation, are counted as skipped rather than
failing the whole import, so a mostly-importable deck still imports.
"""

from __future__ import annotations

from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.domain.anki import AnkiPackage, ImportReport, UnsupportedNote, map_note
from flashcards.domain.decks import MAX_DECK_NAME
from flashcards.domain.notes import InvalidNote
from flashcards.domain.ports import PackageReader
from flashcards.domain.tags import sanitize_tags

_FALLBACK_DECK_NAME = "Imported"


class ApkgImportService:
    def __init__(self, reader: PackageReader, decks: DeckService, notes: NoteService) -> None:
        self._reader = reader
        self._decks = decks
        self._notes = notes

    async def import_package(self, *, owner_id: str, data: bytes) -> ImportReport:
        package = await self._reader.read(data)
        deck_by_note = _deck_id_by_note_guid(package)
        our_deck_id = await self._create_decks(owner_id, package, set(deck_by_note.values()))

        imported = 0
        skipped = 0
        for note in package.notes:
            anki_deck_id = deck_by_note.get(note.guid)
            if anki_deck_id is None:
                continue  # a note with no card has nowhere to live
            note_type = package.note_types.get(note.note_type_id)
            if note_type is None:
                skipped += 1
                continue
            try:
                our_type, fields = map_note(note_type, note.fields)
                await self._notes.create_note(
                    owner_id=owner_id,
                    deck_id=our_deck_id[anki_deck_id],
                    note_type=our_type,
                    fields=dict(fields),
                    tags=sanitize_tags(note.tags),
                )
            except (UnsupportedNote, InvalidNote):
                skipped += 1
                continue
            imported += 1

        return ImportReport(
            decks_created=len(our_deck_id), notes_imported=imported, notes_skipped=skipped
        )

    async def _create_decks(
        self, owner_id: str, package: AnkiPackage, anki_deck_ids: set[int]
    ) -> dict[int, str]:
        our_deck_id: dict[int, str] = {}
        for anki_deck_id in sorted(anki_deck_ids):
            anki_deck = package.decks.get(anki_deck_id)
            name = anki_deck.name if anki_deck is not None else ""
            deck = await self._decks.create(owner_id=owner_id, name=_clean_deck_name(name))
            our_deck_id[anki_deck_id] = deck.id
        return our_deck_id


def _deck_id_by_note_guid(package: AnkiPackage) -> dict[str, int]:
    # A note's first card decides which deck it lands in.
    result: dict[str, int] = {}
    for card in package.cards:
        result.setdefault(card.note_guid, card.deck_id)
    return result


def _clean_deck_name(name: str) -> str:
    cleaned = name.strip()[:MAX_DECK_NAME]
    return cleaned or _FALLBACK_DECK_NAME
