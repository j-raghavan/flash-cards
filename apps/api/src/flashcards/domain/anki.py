# SPDX-License-Identifier: AGPL-3.0-only
"""Parsed Anki package content and its mapping to our note model (F-IMP-1).

These are the framework-free value objects a defensive .apkg reader produces,
plus the pure rule that maps an Anki note onto our Basic / Cloze note types.
Anki's cloze syntax is already {{c1::...}}, so a cloze note maps across directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from flashcards.domain.notes import Fields, NoteType


class AnkiImportError(Exception):
    """Base class for .apkg import failures."""


class InvalidPackage(AnkiImportError):
    """The file is not a readable Anki package."""


class UnsupportedPackage(AnkiImportError):
    """A readable package in a form we cannot import."""


class UnsupportedNote(AnkiImportError):
    """A note whose type does not map onto Basic or Cloze."""


@dataclass(frozen=True, slots=True)
class AnkiNoteType:
    id: int
    name: str
    is_cloze: bool
    field_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnkiDeck:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class AnkiNote:
    guid: str
    note_type_id: int
    fields: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnkiCard:
    note_guid: str
    deck_id: int
    ordinal: int


@dataclass(frozen=True, slots=True)
class AnkiPackage:
    note_types: dict[int, AnkiNoteType]
    decks: dict[int, AnkiDeck]
    notes: tuple[AnkiNote, ...]
    cards: tuple[AnkiCard, ...]


@dataclass(frozen=True, slots=True)
class ImportReport:
    decks_created: int
    notes_imported: int
    notes_skipped: int  # unknown or unsupported note types, or invalid fields


def map_note(note_type: AnkiNoteType, fields: tuple[str, ...]) -> tuple[NoteType, Fields]:
    """Map an Anki note onto our (NoteType, Fields), or raise UnsupportedNote."""
    if note_type.is_cloze:
        if not fields or not fields[0].strip():
            raise UnsupportedNote("cloze note has no text")
        return NoteType.CLOZE, (("text", fields[0]),)
    if len(fields) >= 2:
        # Anki's first two fields are the question and answer for a basic note.
        return NoteType.BASIC, (("front", fields[0]), ("back", fields[1]))
    raise UnsupportedNote(f"note type {note_type.name!r} has too few fields to import")
