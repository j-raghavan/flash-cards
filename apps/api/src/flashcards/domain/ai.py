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
from dataclasses import dataclass, replace
from datetime import datetime

from flashcards.domain.figures import FIGURE_SCALE, MAX_FIGURES, FigureBox, InvalidFigure
from flashcards.domain.notes import Fields, InvalidNote, NoteType, card_ordinals

MAX_DRAFT_CARDS = 200


class LlmError(Exception):
    """Transport or provider failure while generating."""


class InvalidModelOutput(LlmError):
    """The provider answered, but its output did not satisfy the domain's schema."""


class OutputTruncated(InvalidModelOutput):
    """The answer was cut off at the output limit. The provider still billed the
    call, so the usage it reported travels with the error to be recorded."""

    def __init__(self, usage: TokenUsage, model: str) -> None:
        super().__init__("the model output was truncated at the output limit")
        self.usage = usage
        self.model = model


class InvalidDraft(InvalidModelOutput):
    """The model output did not match the draft-card schema or had unusable fields."""


class DraftNotFound(Exception):
    """A draft card that does not exist, or is not the caller's / not in this job."""


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
    figure: int = 0  # the 1-based figure of the page the card shows; 0 for none


@dataclass(frozen=True, slots=True)
class DraftOutput:
    """One page's validated output: its cards and the figures they show."""

    cards: tuple[DraftCard, ...]
    figures: tuple[FigureBox | None, ...]  # None: a listed box that was unusable


@dataclass(frozen=True, slots=True)
class DraftCardRecord:
    """A generated draft card awaiting the user's review (F-AI-4)."""

    id: str
    job_id: str
    owner_id: str
    note_type: NoteType
    fields: Fields
    source_page: int
    created_at: datetime


# The fields each draft type may carry, matching DRAFT_CARDS_SCHEMA. The parser
# enforces them too, for servers that do not honour the schema strictly.
_DRAFT_FIELDS: Mapping[NoteType, frozenset[str]] = {
    NoteType.BASIC: frozenset({"front", "back"}),
    NoteType.CLOZE: frozenset({"text"}),
}


def _draft_shape(note_type: NoteType, field_names: tuple[str, ...]) -> dict[str, object]:
    """One card shape: its type, exactly the fields that type takes, and its figure."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "fields", "figure"],
        "properties": {
            "type": {"type": "string", "enum": [note_type.value]},
            "fields": {
                "type": "object",
                "additionalProperties": False,
                "required": list(field_names),
                "properties": {name: {"type": "string"} for name in field_names},
            },
            "figure": {"type": "integer"},
        },
    }


_BOX_EDGES = ("left", "top", "right", "bottom")


# The JSON Schema a provider is told to produce. Kept framework-free so an
# adapter can hand it to any structured-output API unchanged. Drafts are Basic or
# Cloze only (ADR 0013). Every object closes its keys (additionalProperties false)
# and lists them all as required, which strict structured outputs demand on both
# OpenAI and Anthropic, so each card type is spelled out as its own shape. A
# card's figure is the 1-based number of the page figure it shows (0 for none),
# and each figure is its box on the page image (domain.figures).
DRAFT_CARDS_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cards", "figures"],
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "anyOf": [
                    _draft_shape(NoteType.BASIC, ("front", "back")),
                    _draft_shape(NoteType.CLOZE, ("text",)),
                ]
            },
        },
        "figures": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(_BOX_EDGES),
                "properties": {edge: {"type": "integer"} for edge in _BOX_EDGES},
            },
        },
    },
}


def parse_draft_output(raw: Mapping[str, object], *, source_page: int) -> DraftOutput:
    """Validate a page's raw model output into its cards and figures.

    The cards are held to the schema (InvalidDraft). Figures are only an aid: a
    box's edges are clipped to the page, and one that is then not a rectangle, or
    is past MAX_FIGURES, is dropped,
    and a card naming a missing or dropped figure keeps its text alone, so one
    bad box never costs the page its cards. Output without the figure keys (an
    older or lenient server) reads as having no figures.
    """
    figures = _parse_figures(raw.get("figures"))
    cards = [
        card
        if 0 < card.figure <= len(figures) and figures[card.figure - 1] is not None
        else replace(card, figure=0)
        for card in parse_draft_cards(raw, source_page=source_page)
    ]
    return DraftOutput(cards=tuple(cards), figures=figures)


def _parse_figures(raw: object) -> tuple[FigureBox | None, ...]:
    """Each listed figure's box, or None where it is unusable, keeping their numbers."""
    if not isinstance(raw, list):
        return ()
    return tuple(_parse_box(item) for item in raw[:MAX_FIGURES])


def _parse_box(item: object) -> FigureBox | None:
    if not isinstance(item, dict):
        return None
    edges: list[int] = []
    for edge in _BOX_EDGES:
        value = item.get(edge)
        # bool is an int subclass; a true/false edge is not a coordinate.
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        # A box running off the page is clipped to it: models overshoot the edge.
        edges.append(min(max(value, 0), FIGURE_SCALE))
    try:
        return FigureBox(*edges)
    except InvalidFigure:
        return None


def parse_draft_cards(raw: Mapping[str, object], *, source_page: int) -> list[DraftCard]:
    """Validate raw model output into DraftCards, raising InvalidDraft on anything off.

    An empty list is valid: a cover or instructions page has nothing to study,
    and the prompt asks for no cards about the document itself.
    """
    cards_raw = raw.get("cards")
    if not isinstance(cards_raw, list):
        raise InvalidDraft("output has no cards list")
    if len(cards_raw) > MAX_DRAFT_CARDS:
        raise InvalidDraft("output has too many cards")

    drafts: list[DraftCard] = []
    for item in cards_raw:
        if not isinstance(item, dict):
            raise InvalidDraft("card is not an object")
        type_value = item.get("type")
        fields_value = item.get("fields")
        figure = item.get("figure", 0)
        if not isinstance(figure, int) or isinstance(figure, bool):
            figure = 0  # not a figure number: the card keeps its text alone
        if type_value not in (NoteType.BASIC.value, NoteType.CLOZE.value):
            raise InvalidDraft(f"unknown card type: {type_value!r}")
        if not isinstance(fields_value, dict):
            raise InvalidDraft("card fields is not an object")
        for key, value in fields_value.items():
            # Reject, don't coerce: a null/number/object value must not become a card.
            if not isinstance(key, str) or not isinstance(value, str):
                raise InvalidDraft("card fields must be string key/value pairs")
        note_type = NoteType(type_value)
        if not set(fields_value) <= _DRAFT_FIELDS[note_type]:
            raise InvalidDraft(f"unexpected fields for a {note_type.value} card")
        fields: Fields = tuple(fields_value.items())
        try:
            card_ordinals(note_type, fields)
        except InvalidNote as exc:
            raise InvalidDraft(str(exc)) from exc
        drafts.append(
            DraftCard(note_type=note_type, fields=fields, source_page=source_page, figure=figure)
        )
    return drafts
