# SPDX-License-Identifier: AGPL-3.0-only
"""Notes and cards. A note holds the content; cards are what gets scheduled.

Basic notes make one card (ordinal 0). Cloze notes make one card per distinct
deletion number found in the text ({{c1::...}}, {{c2::...}}), so a note with two
deletions yields two cards (F-CARD-1, F-CARD-2).
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, replace
from datetime import datetime

MAX_FIELD_LEN = 20_000
MAX_FIELDS = 64
# A full {{cN::...}} deletion with a closing brace. N is a positive integer:
# leading zeros are allowed (c01 == c1) but c0/c00 are not valid deletions.
_CLOZE = re.compile(r"\{\{c(0*[1-9]\d*)::.*?\}\}", re.DOTALL)


class NoteType(enum.StrEnum):
    BASIC = "basic"
    CLOZE = "cloze"


class CardError(Exception):
    """Base class for note/card failures."""


class InvalidNote(CardError):
    pass


class NoteNotFound(CardError):
    """A note that does not exist or is not the caller's."""


class CardNotFound(CardError):
    """A card that does not exist or is not the caller's."""


Fields = tuple[tuple[str, str], ...]


def _field(fields: Fields, name: str) -> str | None:
    for key, value in fields:
        if key == name:
            return value
    return None


def _require_nonempty(fields: Fields, name: str) -> str:
    # Length is already bounded by _validate_payload, which runs first.
    value = (_field(fields, name) or "").strip()
    if not value:
        raise InvalidNote(f"field '{name}' is required")
    return value


def _validate_payload(fields: Fields) -> None:
    """Bound the whole payload, not just the fields a note type reads."""
    if len(fields) > MAX_FIELDS:
        raise InvalidNote(f"too many fields (max {MAX_FIELDS})")
    for name, value in fields:
        if len(value) > MAX_FIELD_LEN:
            raise InvalidNote(f"field '{name}' is too long")


def parse_cloze_ordinals(text: str) -> list[int]:
    ordinals = sorted({int(m.group(1)) for m in _CLOZE.finditer(text)})
    if not ordinals:
        raise InvalidNote("cloze text must contain at least one {{cN::...}} deletion")
    return ordinals


def card_ordinals(note_type: NoteType, fields: Fields) -> list[int]:
    """Validate the fields for the note type and return the ordinals to make cards for."""
    _validate_payload(fields)
    if note_type is NoteType.BASIC:
        _require_nonempty(fields, "front")
        _require_nonempty(fields, "back")
        return [0]
    text = _require_nonempty(fields, "text")
    return parse_cloze_ordinals(text)


@dataclass(frozen=True, slots=True)
class Note:
    id: str
    owner_id: str
    deck_id: str
    note_type: NoteType
    fields: Fields
    created_at: datetime

    def field(self, name: str) -> str | None:
        return _field(self.fields, name)


@dataclass(frozen=True, slots=True)
class Card:
    id: str
    owner_id: str
    deck_id: str
    note_id: str
    ordinal: int
    created_at: datetime
    suspended_at: datetime | None = None

    @property
    def is_suspended(self) -> bool:
        return self.suspended_at is not None

    def suspended(self, now: datetime) -> Card:
        return replace(self, suspended_at=now)

    def unsuspended(self) -> Card:
        return replace(self, suspended_at=None)
