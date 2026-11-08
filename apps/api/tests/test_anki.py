# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest

from flashcards.domain.anki import AnkiNoteType, UnsupportedNote, map_note
from flashcards.domain.notes import NoteType

BASIC_TYPE = AnkiNoteType(id=1, name="Basic", is_cloze=False, field_names=("Front", "Back"))
CLOZE_TYPE = AnkiNoteType(id=2, name="Cloze", is_cloze=True, field_names=("Text", "Extra"))


def test_basic_note_maps_front_and_back() -> None:
    note_type, fields = map_note(BASIC_TYPE, ("Capital of France?", "Paris"))
    assert note_type is NoteType.BASIC
    assert fields == (("front", "Capital of France?"), ("back", "Paris"))


def test_extra_fields_beyond_two_are_dropped_for_basic() -> None:
    note_type, fields = map_note(BASIC_TYPE, ("Q", "A", "source", "notes"))
    assert note_type is NoteType.BASIC
    assert fields == (("front", "Q"), ("back", "A"))


def test_cloze_note_carries_the_text_across() -> None:
    note_type, fields = map_note(CLOZE_TYPE, ("The {{c1::mitochondria}} is the powerhouse", ""))
    assert note_type is NoteType.CLOZE
    assert fields == (("text", "The {{c1::mitochondria}} is the powerhouse"),)


def test_cloze_without_text_is_unsupported() -> None:
    with pytest.raises(UnsupportedNote):
        map_note(CLOZE_TYPE, ("   ",))


def test_single_field_basic_is_unsupported() -> None:
    single = AnkiNoteType(id=3, name="Prompt", is_cloze=False, field_names=("Only",))
    with pytest.raises(UnsupportedNote):
        map_note(single, ("just one",))
