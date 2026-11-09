# SPDX-License-Identifier: AGPL-3.0-only
"""A deck and its subdecks gathered for export (F-IMP-4, ADR 0013).

The application service collects one owner's deck tree into a DeckExport; a
DeckExportWriter adapter turns it into .apkg, CSV or JSON bytes.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from datetime import datetime

from flashcards.domain.notes import Card, Note
from flashcards.domain.scheduling import CardSchedule, ReviewLog

MAX_EXPORT_CARDS = 200_000  # the same bound the .apkg reader puts on an import
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_FILENAME_STEM = 80


class ExportTooLarge(Exception):
    """The deck tree has more cards than one export may hold."""


class ExportFormat(enum.StrEnum):
    APKG = "apkg"
    CSV = "csv"
    JSON = "json"

    @property
    def media_type(self) -> str:
        return _MEDIA_TYPES[self]


_MEDIA_TYPES = {
    ExportFormat.APKG: "application/octet-stream",
    ExportFormat.CSV: "text/csv; charset=utf-8",
    ExportFormat.JSON: "application/json",
}


@dataclass(frozen=True, slots=True)
class ExportedCard:
    card: Card
    schedule: CardSchedule | None
    reviews: tuple[ReviewLog, ...]  # oldest first


@dataclass(frozen=True, slots=True)
class ExportedNote:
    note: Note
    deck: tuple[str, ...]  # deck path from the exported root: ("Spanish", "Verbs")
    cards: tuple[ExportedCard, ...]  # by ordinal


@dataclass(frozen=True, slots=True)
class DeckExport:
    decks: tuple[tuple[str, ...], ...]  # every deck path in the tree, parents first
    notes: tuple[ExportedNote, ...]
    exported_at: datetime


@dataclass(frozen=True, slots=True)
class ExportFile:
    filename: str
    media_type: str
    data: bytes


def export_filename(deck_name: str, export_format: ExportFormat) -> str:
    """A safe ASCII download name for the deck: letters, digits, dot, dash, underscore."""
    stem = _UNSAFE_FILENAME.sub("-", deck_name).strip("-.")[:_MAX_FILENAME_STEM].strip("-.")
    return f"{stem or 'deck'}.{export_format.value}"
