# SPDX-License-Identifier: AGPL-3.0-only
"""Parsed Anki package content and its mapping to our note model (F-IMP-1..3).

These are the framework-free value objects a defensive .apkg reader produces,
plus the pure rules that map an Anki note onto our Basic / Cloze note types and
select part of a package. Anki's cloze syntax is already {{c1::...}}, so a cloze
note maps across directly. Scheduling state is mapped in anki_schedule.py.
"""

from __future__ import annotations

import enum
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


DECK_SEPARATOR = "::"  # Anki nests decks by name: "Languages::Spanish"


@dataclass(frozen=True, slots=True)
class AnkiNoteType:
    id: int
    name: str
    is_cloze: bool
    field_names: tuple[str, ...]
    template_count: int = 1  # card templates; "Basic (and reversed card)" has two


@dataclass(frozen=True, slots=True)
class AnkiDeck:
    id: int
    name: str
    is_filtered: bool = False  # a filtered ("dynamic") deck only borrows cards


@dataclass(frozen=True, slots=True)
class AnkiNote:
    guid: str
    note_type_id: int
    fields: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnkiCard:
    """One Anki card with its SM-2 scheduling columns (all zero for a new card).

    `card_type` is Anki's cards.type (0 new, 1 learning, 2 review, 3 relearning) and
    `queue` its cards.queue (-1 suspended, -2/-3 buried, otherwise the type's queue).
    For a review card `due` is a day number counted from the collection's creation
    day and `interval` is in days; `factor` is the ease in permille (2500 = 250%).
    """

    note_guid: str
    deck_id: int
    ordinal: int
    id: int = 0
    card_type: int = 0
    queue: int = 0
    due: int = 0
    interval: int = 0
    factor: int = 0
    reps: int = 0
    lapses: int = 0
    original_deck_id: int = 0  # set while the card sits in a filtered deck
    flag: int = 0


@dataclass(frozen=True, slots=True)
class AnkiReview:
    """One revlog row: `reviewed_at_ms` is the row id (epoch milliseconds)."""

    card_id: int
    reviewed_at_ms: int
    ease: int  # the answer button, 1 (again) to 4 (easy); 0 for a manual change
    kind: int  # 0 learn, 1 review, 2 relearn, 3 filtered, 4 manual, 5 rescheduled


@dataclass(frozen=True, slots=True)
class AnkiPackage:
    note_types: dict[int, AnkiNoteType]
    decks: dict[int, AnkiDeck]
    notes: tuple[AnkiNote, ...]
    cards: tuple[AnkiCard, ...]
    reviews: tuple[AnkiReview, ...] = ()
    created_at_s: int | None = None  # col.crt, epoch seconds: day 0 for review dues
    media_count: int = 0  # media files in the archive (not imported)
    history_truncated: bool = False  # revlog over the read limit, so none was kept


MAX_SELECTION_ITEMS = 50
MAX_SELECTION_LENGTH = 200


@dataclass(frozen=True, slots=True)
class ImportSelection:
    """Which part of a package to import. Empty means everything.

    A note is selected when its deck is one of `decks` or a sub-deck of one (names
    compared ignoring case), and it carries one of `tags` or a descendant of one.
    """

    decks: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


class UnsupportedKind(enum.StrEnum):
    UNKNOWN_NOTE_TYPE = "unknown_note_type"  # the note's type is missing from the package
    UNMAPPABLE_NOTE = "unmappable_note"  # too few fields for Basic, or a cloze with no text
    INVALID_NOTE = "invalid_note"  # fails our note validation (length, cloze markers)
    DROPPED_FIELDS = "dropped_fields"  # non-empty fields beyond the ones we map
    EXTRA_CARDS = "extra_cards"  # further templates or empty cloze deletions: no card here
    FILTERED_DECK = "filtered_deck"  # cards placed in their original deck instead
    MEDIA = "media"  # media files are not imported
    NOTE_WITHOUT_CARDS = "note_without_cards"
    SKIPPED_REVIEWS = "skipped_reviews"  # manual or rescheduling entries, bad times
    HISTORY_TRUNCATED = "history_truncated"  # revlog too large; schedules from SM-2 fields


@dataclass(frozen=True, slots=True)
class UnsupportedConstruct:
    kind: UnsupportedKind
    count: int
    examples: tuple[str, ...] = ()  # a few note type or deck names, for the user


@dataclass(frozen=True, slots=True)
class ImportReport:
    decks_created: int
    notes_imported: int
    notes_skipped: int  # unknown or unsupported note types, or invalid fields
    notes_not_selected: int = 0
    cards_scheduled: int = 0  # cards that arrive with a schedule rather than as new
    reviews_imported: int = 0
    dry_run: bool = False
    unsupported: tuple[UnsupportedConstruct, ...] = ()


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


def has_dropped_fields(note_type: AnkiNoteType, fields: tuple[str, ...]) -> bool:
    """Whether map_note leaves out a field that has content."""
    kept = 1 if note_type.is_cloze else 2
    return any(value.strip() for value in fields[kept:])


def our_ordinal(note_type: AnkiNoteType, anki_ordinal: int) -> int | None:
    """Our card ordinal for an Anki card's `ord`, or None when it has no counterpart.

    A Basic note has one card (ordinal 0), so further templates are dropped. An Anki
    cloze card's ord is its deletion number minus one; ours is the number itself.
    """
    if note_type.is_cloze:
        return anki_ordinal + 1 if anki_ordinal >= 0 else None
    return 0 if anki_ordinal == 0 else None


def deck_path(name: str) -> tuple[str, ...]:
    """An Anki deck name split into its levels."""
    return tuple(name.split(DECK_SEPARATOR))


def in_selected_decks(name: str, wanted: tuple[str, ...]) -> bool:
    """Whether a deck is one of `wanted` or nested under one, ignoring case."""
    if not wanted:
        return True
    folded = name.strip().casefold()
    for deck in wanted:
        target = deck.strip().casefold()
        if folded == target or folded.startswith(target + DECK_SEPARATOR):
            return True
    return False
