# SPDX-License-Identifier: AGPL-3.0-only
"""Every structured-output schema meets the strict rules both providers enforce.

OpenAI's strict json_schema mode and Anthropic's structured outputs both refuse a
schema whose objects leave keys open (additionalProperties other than false), and
OpenAI also refuses one whose required list does not name every property. A
schema that breaks either rule fails every request with HTTP 400.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from flashcards.domain.ai import DRAFT_CARDS_SCHEMA, InvalidDraft, parse_draft_cards
from flashcards.domain.chat import CHAT_SCHEMA
from flashcards.domain.evaluation import EVALUATION_SCHEMA
from flashcards.domain.mock_exam import EXAM_SCHEMA, GRADE_SCHEMA
from flashcards.domain.notes import NoteType
from flashcards.domain.summary import SUMMARY_SCHEMA

SCHEMAS: dict[str, Mapping[str, object]] = {
    "draft cards": DRAFT_CARDS_SCHEMA,
    "chat": CHAT_SCHEMA,
    "evaluation": EVALUATION_SCHEMA,
    "mock exam": EXAM_SCHEMA,
    "grade": GRADE_SCHEMA,
    "summary": SUMMARY_SCHEMA,
}


def _strict_violations(node: object, path: str) -> list[str]:
    found: list[str] = []
    if isinstance(node, Mapping):
        if node.get("type") == "object":
            properties = node.get("properties")
            keys = sorted(properties) if isinstance(properties, Mapping) else []
            if node.get("additionalProperties") is not False:
                found.append(f"{path}: additionalProperties is not false")
            required = node.get("required")
            if not isinstance(required, list) or sorted(required) != keys:
                found.append(f"{path}: required does not list every property")
        for key, child in node.items():
            found.extend(_strict_violations(child, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            found.extend(_strict_violations(child, f"{path}[{index}]"))
    return found


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_is_strict_mode_compatible(name: str) -> None:
    assert _strict_violations(SCHEMAS[name], name) == []


def test_the_checker_catches_open_and_partly_required_objects() -> None:
    loose = {
        "type": "object",
        "additionalProperties": {"type": "string"},
        "required": ["a"],
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
    }
    assert _strict_violations(loose, "loose") == [
        "loose: additionalProperties is not false",
        "loose: required does not list every property",
    ]


def test_output_shaped_by_the_draft_schema_parses() -> None:
    raw = {
        "cards": [
            {"type": "basic", "fields": {"front": "Q", "back": "A"}},
            {"type": "cloze", "fields": {"text": "The {{c1::mitochondria}} makes ATP."}},
        ]
    }
    drafts = parse_draft_cards(raw, source_page=3)
    assert [d.note_type for d in drafts] == [NoteType.BASIC, NoteType.CLOZE]
    assert drafts[0].fields == (("front", "Q"), ("back", "A"))
    assert all(d.source_page == 3 for d in drafts)


def _schema_field_sets() -> dict[str, set[str]]:
    """The field names each card type's shape in DRAFT_CARDS_SCHEMA allows."""
    properties = DRAFT_CARDS_SCHEMA["properties"]
    assert isinstance(properties, Mapping)
    cards = properties["cards"]
    assert isinstance(cards, Mapping)
    items = cards["items"]
    assert isinstance(items, Mapping)
    shapes = items["anyOf"]
    assert isinstance(shapes, list)
    found: dict[str, set[str]] = {}
    for shape in shapes:
        type_enum = shape["properties"]["type"]["enum"]
        found[type_enum[0]] = set(shape["properties"]["fields"]["properties"])
    return found


def test_the_schema_and_the_parser_allow_the_same_fields() -> None:
    assert _schema_field_sets() == {"basic": {"front", "back"}, "cloze": {"text"}}
    for note_type, names in _schema_field_sets().items():
        values = {name: "{{c1::x}}" for name in names}
        assert parse_draft_cards({"cards": [{"type": note_type, "fields": values}]}, source_page=1)


@pytest.mark.parametrize(
    "card",
    [
        {"type": "basic", "fields": {"front": "Q", "back": "A", "text": "x"}},
        {"type": "cloze", "fields": {"text": "{{c1::x}}", "extra": "y"}},
        {"type": "basic", "fields": {"text": "{{c1::x}}"}},
    ],
)
def test_the_parser_rejects_fields_the_schema_does_not_allow(card: dict[str, object]) -> None:
    with pytest.raises(InvalidDraft):
        parse_draft_cards({"cards": [card]}, source_page=1)
