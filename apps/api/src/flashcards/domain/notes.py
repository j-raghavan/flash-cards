# SPDX-License-Identifier: AGPL-3.0-only
"""Notes and cards. A note holds the content; cards are what gets scheduled.

Basic notes make one card (ordinal 0). Cloze notes make one card per distinct
deletion number found in the text ({{c1::...}}, {{c2::...}}), so a note with two
deletions yields two cards (F-CARD-1, F-CARD-2). Custom notes make one card per
template of their user-defined type (F-CARD-4, domain.note_types), and image
occlusion notes one card per mask id (F-CARD-3, ADR 0013).
"""

from __future__ import annotations

import enum
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from flashcards.domain.tags import Tags

if TYPE_CHECKING:
    from flashcards.domain.note_types import CustomNoteType

MAX_FIELD_LEN = 20_000
MAX_FLAG = 7
DEFAULT_BURY = timedelta(hours=24)
MAX_BURY = timedelta(days=7)
MAX_FIELDS = 64
MAX_FIELD_NAME_LEN = 64
# A full {{cN::...}} deletion with a closing brace. N is a positive integer:
# leading zeros are allowed (c01 == c1) but c0/c00 are not valid deletions.
_CLOZE = re.compile(r"\{\{c(0*[1-9]\d*)::.*?\}\}", re.DOTALL)
# Image occlusion (ADR 0013): masks sharing an id make one card, as in Anki.
MAX_MASKS = 100
MAX_MASK_ID = 999
MAX_MEDIA_ID_LEN = 64
OCCLUSION_FIELDS = ("header", "back_extra")  # in this order
_EDGE_SLACK = 1e-9  # float sums such as 0.1 + 0.9 may land a hair over 1


class NoteType(enum.StrEnum):
    BASIC = "basic"
    CLOZE = "cloze"
    CUSTOM = "custom"  # a user-defined type with its own fields and templates
    IMAGE_OCCLUSION = "image_occlusion"


class CardError(Exception):
    """Base class for note/card failures."""


class InvalidNote(CardError):
    pass


class NoteNotFound(CardError):
    """A note that does not exist or is not the caller's."""


class InvalidFlag(CardError):
    """A flag outside 0 to MAX_FLAG."""


class InvalidBury(CardError):
    """A bury time in the past or more than MAX_BURY ahead."""


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
        if not 0 < len(name) <= MAX_FIELD_NAME_LEN:
            raise InvalidNote(f"field names must be 1 to {MAX_FIELD_NAME_LEN} characters")
        if len(value) > MAX_FIELD_LEN:
            raise InvalidNote(f"field '{name}' is too long")


def parse_cloze_ordinals(text: str) -> list[int]:
    ordinals = sorted({int(m.group(1)) for m in _CLOZE.finditer(text)})
    if not ordinals:
        raise InvalidNote("cloze text must contain at least one {{cN::...}} deletion")
    return ordinals


@dataclass(frozen=True, slots=True)
class Mask:
    """A rectangle over the image, in coordinates normalized to 0..1 of its size.

    `id` is stable across edits and is the ordinal of the card the mask belongs
    to; several masks with one id are hidden and revealed together.
    """

    id: int
    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class Occlusion:
    image_id: str  # a media asset the note's owner uploaded
    masks: tuple[Mask, ...]

    def ordinals(self) -> list[int]:
        """Validate the masks and return one ordinal per distinct mask id."""
        if not 0 < len(self.image_id) <= MAX_MEDIA_ID_LEN:
            raise InvalidNote("an image occlusion note needs an image")
        if not 0 < len(self.masks) <= MAX_MASKS:
            raise InvalidNote(f"an image occlusion note needs 1 to {MAX_MASKS} masks")
        for mask in self.masks:
            _validate_mask(mask)
        return sorted({mask.id for mask in self.masks})


def _validate_mask(mask: Mask) -> None:
    if not 1 <= mask.id <= MAX_MASK_ID:
        raise InvalidNote(f"mask ids run from 1 to {MAX_MASK_ID}")
    box = (mask.left, mask.top, mask.width, mask.height)
    if not all(math.isfinite(value) for value in box):
        raise InvalidNote("mask coordinates must be finite numbers")
    if mask.left < 0 or mask.top < 0 or mask.width <= 0 or mask.height <= 0:
        raise InvalidNote("masks need a non-negative position and a positive size")
    if mask.left + mask.width > 1 + _EDGE_SLACK or mask.top + mask.height > 1 + _EDGE_SLACK:
        raise InvalidNote("masks must lie within the image")


def note_fields(
    note_type: NoteType, given: Mapping[str, str], custom: CustomNoteType | None = None
) -> Fields:
    """The fields to store: a custom or image occlusion note's in its fixed order.

    Unknown fields of those types are refused (InvalidNote); missing ones are
    stored empty. Basic and Cloze notes keep the fields as given.
    """
    if custom is not None:
        return custom.normalize_fields(given)
    if note_type is NoteType.IMAGE_OCCLUSION:
        if any(name not in OCCLUSION_FIELDS for name in given):
            raise InvalidNote("image occlusion notes have only header and back_extra fields")
        return tuple((name, given.get(name, "")) for name in OCCLUSION_FIELDS)
    return tuple(given.items())


def card_ordinals(
    note_type: NoteType,
    fields: Fields,
    *,
    custom: CustomNoteType | None = None,
    occlusion: Occlusion | None = None,
) -> list[int]:
    """Validate the fields for the note type and return the ordinals to make cards for.

    A custom note needs its type (`custom`) and an image occlusion note its masks
    (`occlusion`); passing either to another note type is an error.
    """
    _validate_payload(fields)
    if note_type is NoteType.CUSTOM:
        if custom is None:
            raise InvalidNote("a custom note needs its note type (note_type_id)")
        return custom.card_ordinals(fields)
    if custom is not None:
        raise InvalidNote("only a custom note has a note type id")
    if note_type is NoteType.IMAGE_OCCLUSION:
        if occlusion is None:
            raise InvalidNote("an image occlusion note needs an image and masks")
        if any(name not in OCCLUSION_FIELDS for name, _value in fields):
            raise InvalidNote("image occlusion notes have only header and back_extra fields")
        return occlusion.ordinals()
    if occlusion is not None:
        raise InvalidNote("only an image occlusion note has an image and masks")
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
    tags: Tags = ()
    note_type_id: str | None = None  # set for (and only for) a custom note
    occlusion: Occlusion | None = None  # set for (and only for) an image occlusion note

    def field(self, name: str) -> str | None:
        return _field(self.fields, name)

    def with_fields(self, fields: Fields) -> Note:
        return replace(self, fields=fields)

    def with_tags(self, tags: Tags) -> Note:
        return replace(self, tags=tags)

    def with_occlusion(self, occlusion: Occlusion) -> Note:
        return replace(self, occlusion=occlusion)

    def moved_to(self, deck_id: str) -> Note:
        return replace(self, deck_id=deck_id)


@dataclass(frozen=True, slots=True)
class Card:
    id: str
    owner_id: str
    deck_id: str
    note_id: str
    ordinal: int
    created_at: datetime
    suspended_at: datetime | None = None
    flag: int = 0  # 0 is no flag; 1 to 7 are Anki's colours
    buried_until: datetime | None = None

    @property
    def is_suspended(self) -> bool:
        return self.suspended_at is not None

    def suspended(self, now: datetime) -> Card:
        return replace(self, suspended_at=now)

    def unsuspended(self) -> Card:
        return replace(self, suspended_at=None)

    def is_buried(self, now: datetime) -> bool:
        return self.buried_until is not None and now < self.buried_until

    def buried(self, now: datetime, until: datetime | None = None) -> Card:
        """Hidden from the review queue until `until` (24 hours when not given)."""
        until = until if until is not None else now + DEFAULT_BURY
        if not now < until <= now + MAX_BURY:
            raise InvalidBury("a card can be buried for up to 7 days")
        return replace(self, buried_until=until.astimezone(UTC))  # stored the same everywhere

    def unburied(self) -> Card:
        return replace(self, buried_until=None)

    def with_flag(self, flag: int) -> Card:
        if not 0 <= flag <= MAX_FLAG:
            raise InvalidFlag(f"flags run from 0 to {MAX_FLAG}")
        return replace(self, flag=flag)

    def moved_to(self, deck_id: str) -> Card:
        return replace(self, deck_id=deck_id)
