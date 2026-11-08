# SPDX-License-Identifier: AGPL-3.0-only
"""AI generation value objects: the structured-generation contract and draft cards.

The domain owns the output schema and the parser, so every provider adapter is
constrained to the same shape and its output is re-validated into domain objects
before anything is trusted (NF-SEC-6). Uploaded material travels as documents,
never concatenated into instructions.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass

from flashcards.domain.notes import Fields, InvalidNote, NoteType, card_ordinals

MAX_DRAFT_CARDS = 200


class LlmError(Exception):
    """Transport or provider failure while generating."""


class InvalidDraft(LlmError):
    """The model output did not match the draft-card schema or had unusable fields."""


class Thoroughness(enum.StrEnum):
    CONCISE = "concise"
    STANDARD = "standard"
    THOROUGH = "thorough"


@dataclass(frozen=True, slots=True)
class LlmDocument:
    media_type: str  # application/pdf | image/png | image/jpeg | text/plain
    data: bytes


@dataclass(frozen=True, slots=True)
class StructuredRequest:
    instructions: str  # application-authored; holds no uploaded content
    documents: tuple[LlmDocument, ...]
    schema: Mapping[str, object]  # JSON Schema; additionalProperties:false
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class StructuredResult:
    data: Mapping[str, object]  # parsed JSON, already schema-shaped
    usage: TokenUsage
    model: str


@dataclass(frozen=True, slots=True)
class DraftCard:
    note_type: NoteType
    fields: Fields
    source_page: int  # the 1-based page we sent, for the card's source reference (F-AI-5)


# The JSON Schema a provider is told to produce. Kept framework-free so an
# adapter can hand it to any structured-output API unchanged.
DRAFT_CARDS_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cards"],
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "fields"],
                "properties": {
                    "type": {"type": "string", "enum": [t.value for t in NoteType]},
                    "fields": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                },
            },
        }
    },
}


def parse_draft_cards(raw: Mapping[str, object], *, source_page: int) -> list[DraftCard]:
    """Validate raw model output into DraftCards, raising InvalidDraft on anything off."""
    cards_raw = raw.get("cards")
    if not isinstance(cards_raw, list) or not cards_raw:
        raise InvalidDraft("output has no cards")
    if len(cards_raw) > MAX_DRAFT_CARDS:
        raise InvalidDraft("output has too many cards")

    drafts: list[DraftCard] = []
    for item in cards_raw:
        if not isinstance(item, dict):
            raise InvalidDraft("card is not an object")
        type_value = item.get("type")
        fields_value = item.get("fields")
        if type_value not in (NoteType.BASIC.value, NoteType.CLOZE.value):
            raise InvalidDraft(f"unknown card type: {type_value!r}")
        if not isinstance(fields_value, dict):
            raise InvalidDraft("card fields is not an object")
        for key, value in fields_value.items():
            # Reject, don't coerce: a null/number/object value must not become a card.
            if not isinstance(key, str) or not isinstance(value, str):
                raise InvalidDraft("card fields must be string key/value pairs")
        note_type = NoteType(type_value)
        fields: Fields = tuple(fields_value.items())
        try:
            card_ordinals(note_type, fields)
        except InvalidNote as exc:
            raise InvalidDraft(str(exc)) from exc
        drafts.append(DraftCard(note_type=note_type, fields=fields, source_page=source_page))
    return drafts
