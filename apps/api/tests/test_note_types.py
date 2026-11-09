# SPDX-License-Identifier: AGPL-3.0-only
"""Custom note type rules: fields, templates and stable ordinals (ADR 0013)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from flashcards.domain.note_types import (
    MAX_TEMPLATE_LEN,
    MAX_TEMPLATE_ORDINAL,
    MAX_TEMPLATES,
    CardTemplate,
    CustomNoteType,
    InvalidNoteType,
    TemplateSpec,
    build_templates,
    edit_note_type,
    field_sources,
    new_note_type,
    placeholders,
    remap_fields,
)
from flashcards.domain.notes import MAX_FIELDS, InvalidNote, NoteType, card_ordinals

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
FORWARD = TemplateSpec("Forward", "{{Word}}", "{{FrontSide}}<hr>{{Meaning}}")
REVERSE = TemplateSpec("Reverse", "{{Meaning}}", "{{FrontSide}}<hr>{{Word}}")


def _vocab(templates: tuple[TemplateSpec, ...] = (FORWARD, REVERSE)) -> CustomNoteType:
    return new_note_type(
        id="t1",
        owner_id="alice",
        name="  Vocabulary  ",
        fields=["Word", "Meaning", "Example"],
        templates=templates,
        created_at=NOW,
    )


def test_a_new_type_gets_ordinals_in_template_order() -> None:
    vocab = _vocab()
    assert vocab.name == "Vocabulary"  # trimmed
    assert vocab.fields == ("Word", "Meaning", "Example")
    assert [(t.ordinal, t.name) for t in vocab.templates] == [(0, "Forward"), (1, "Reverse")]
    assert vocab.template(1) == vocab.templates[1]
    assert vocab.template(7) is None


def test_placeholders_lists_the_names_in_order() -> None:
    assert placeholders("{{A}} and {{B}}{{A}}") == ["A", "B", "A"]


def test_a_card_is_made_for_each_template_whose_front_has_content() -> None:
    vocab = _vocab()
    both = vocab.normalize_fields({"Word": "chat", "Meaning": "cat"})
    assert both == (("Word", "chat"), ("Meaning", "cat"), ("Example", ""))
    assert vocab.card_ordinals(both) == [0, 1]
    only_word = vocab.normalize_fields({"Word": "chat", "Meaning": "  "})
    assert vocab.card_ordinals(only_word) == [0]  # Anki skips an empty front
    assert card_ordinals(NoteType.CUSTOM, both, custom=vocab) == [0, 1]


def test_a_note_without_any_card_is_refused() -> None:
    vocab = _vocab()
    with pytest.raises(InvalidNote, match="no cards"):
        vocab.card_ordinals(vocab.normalize_fields({"Example": "only this"}))


def test_unknown_fields_are_refused() -> None:
    vocab = _vocab()
    with pytest.raises(InvalidNote, match="does not define"):
        vocab.normalize_fields({"Nope": "x"})
    with pytest.raises(InvalidNote, match="does not define"):
        vocab.card_ordinals((("Word", "x"), ("Nope", "y")))


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ([], "1 to"),
        ([f"F{i}" for i in range(MAX_FIELDS + 1)], "1 to"),
        ([""], "field 1"),
        ([" Word"], "field 1"),
        (["A:B"], "field 1"),
        (["A{B"], "field 1"),
        (["Tab​"], "field 1"),
        (["x" * 65], "field 1"),
        (["frontside"], "reserved"),
        (["Word", "word"], "field 2: field names must be unique"),
    ],
)
def test_field_names_are_validated(fields: list[str], message: str) -> None:
    with pytest.raises(InvalidNoteType, match=message):
        new_note_type(
            id="t", owner_id="a", name="T", fields=fields, templates=[FORWARD], created_at=NOW
        )


@pytest.mark.parametrize("name", ["", "   ", "x" * 101, "bad\x00name"])
def test_type_names_are_validated(name: str) -> None:
    with pytest.raises(InvalidNoteType, match="note type name"):
        new_note_type(
            id="t", owner_id="a", name=name, fields=["Word"], templates=[], created_at=NOW
        )


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (TemplateSpec("", "{{Word}}", "x"), "template 1: names"),
        (TemplateSpec("x" * 65, "{{Word}}", "x"), "template 1: names"),
        (TemplateSpec("Card", "{{Word}}" + "x" * MAX_TEMPLATE_LEN, "x"), "front is longer"),
        (TemplateSpec("Card", "{{Word}}", "x" * (MAX_TEMPLATE_LEN + 1)), "back is longer"),
        (TemplateSpec("Card", "{{Missing}}", "x"), "front uses a placeholder"),
        (TemplateSpec("Card", "{{FrontSide}}", "x"), "front uses a placeholder"),
        (TemplateSpec("Card", "{{Word}}", "{{Missing}}"), "back uses a placeholder"),
        (TemplateSpec("Card", "{{#Word}}x{{/Word}}", "x"), "front uses a placeholder"),
        (TemplateSpec("Card", "{{Word}} {{", "x"), "front has an unmatched"),
        (TemplateSpec("Card", "{{Word}}", "}} x"), "back has an unmatched"),
        (TemplateSpec("Card", "no fields", "x"), "front must use at least one field"),
        (TemplateSpec("Card", "{{Word}}", "x", ordinal=0), "ordinal must name"),
    ],
)
def test_templates_are_validated(spec: TemplateSpec, message: str) -> None:
    with pytest.raises(InvalidNoteType, match=message):
        build_templates([spec], ("Word",))


def test_template_count_and_names_are_bounded() -> None:
    with pytest.raises(InvalidNoteType, match="1 to"):
        build_templates([], ("Word",))
    many = [TemplateSpec(f"C{i}", "{{Word}}", "") for i in range(MAX_TEMPLATES + 1)]
    with pytest.raises(InvalidNoteType, match="1 to"):
        build_templates(many, ("Word",))
    twins = [TemplateSpec("Card", "{{Word}}", ""), TemplateSpec("card", "{{Word}}", "")]
    with pytest.raises(InvalidNoteType, match="template 2: template names must be unique"):
        build_templates(twins, ("Word",))


def test_kept_ordinals_survive_and_new_ones_never_reuse_old_numbers() -> None:
    vocab = _vocab()
    # Drop Reverse (1), keep Forward (0), add two: they get 2 and 3, never 1.
    templates = build_templates(
        [
            TemplateSpec("Typed", "{{Word}}", "{{Meaning}}"),
            TemplateSpec("Forward", "{{Word}}", "{{Meaning}}", ordinal=0),
            TemplateSpec("Example", "{{Example}}", "{{Word}}"),
        ],
        vocab.fields,
        vocab.templates,
    )
    named = [(t.ordinal, t.name) for t in templates]
    assert named == [(2, "Typed"), (0, "Forward"), (3, "Example")]


def test_an_ordinal_cannot_be_kept_twice() -> None:
    vocab = _vocab()
    with pytest.raises(InvalidNoteType, match="template 2: the ordinal"):
        build_templates(
            [
                TemplateSpec("A", "{{Word}}", "", ordinal=0),
                TemplateSpec("B", "{{Word}}", "", ordinal=0),
            ],
            vocab.fields,
            vocab.templates,
        )


def test_ordinals_are_bounded() -> None:
    worn = (CardTemplate(ordinal=MAX_TEMPLATE_ORDINAL, name="Old", front="{{Word}}", back=""),)
    with pytest.raises(InvalidNoteType, match="run out"):
        build_templates([TemplateSpec("New", "{{Word}}", "")], ("Word",), worn)


def test_field_sources_follow_renames_and_keep_unchanged_names() -> None:
    old = ("Word", "Meaning", "Example")
    sources = field_sources(old, ("Term", "Meaning", "Word", "Notes"), {"Word": "Term"})
    # Term takes Word's values; Word was renamed away, so the new Word starts empty.
    assert sources == {"Term": "Word", "Meaning": "Meaning", "Word": None, "Notes": None}
    moved = remap_fields((("Word", "chat"), ("Meaning", "cat"), ("Example", "x")), sources)
    assert moved == (("Term", "chat"), ("Meaning", "cat"), ("Word", ""), ("Notes", ""))


def test_fields_can_swap_names() -> None:
    sources = field_sources(("A", "B"), ("A", "B"), {"A": "B", "B": "A"})
    assert sources == {"A": "B", "B": "A"}


@pytest.mark.parametrize(
    ("renamed", "message"),
    [
        ({"Nope": "Term"}, "not a field"),
        ({"Word": "Meaning"}, "another field that is kept"),
        ({"Word": "Elsewhere"}, "one distinct"),
        ({"Word": "Term", "Meaning": "Term"}, "one distinct"),
    ],
)
def test_bad_renames_are_refused(renamed: dict[str, str], message: str) -> None:
    with pytest.raises(InvalidNoteType, match=message):
        field_sources(("Word", "Meaning"), ("Term", "Meaning"), renamed)


def test_editing_keeps_what_is_omitted() -> None:
    vocab = _vocab()
    renamed, sources = edit_note_type(vocab, name="Words")
    assert renamed.name == "Words" and renamed.templates == vocab.templates
    assert sources == {"Word": "Word", "Meaning": "Meaning", "Example": "Example"}


def test_a_rename_needs_the_new_field_list() -> None:
    with pytest.raises(InvalidNoteType, match="new list of fields"):
        edit_note_type(_vocab(), renamed={"Word": "Meaning"})


def test_editing_fields_rechecks_the_existing_templates() -> None:
    vocab = _vocab()
    with pytest.raises(InvalidNoteType, match="placeholder"):
        edit_note_type(vocab, fields=["Term", "Meaning"], renamed={"Word": "Term"})
    edited, sources = edit_note_type(
        vocab,
        fields=["Term", "Meaning"],
        renamed={"Word": "Term"},
        templates=[TemplateSpec("Forward", "{{Term}}", "{{Meaning}}", ordinal=0)],
    )
    assert edited.fields == ("Term", "Meaning")
    assert [t.ordinal for t in edited.templates] == [0]
    assert sources == {"Term": "Word", "Meaning": "Meaning"}
