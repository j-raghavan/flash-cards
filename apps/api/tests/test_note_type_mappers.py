# SPDX-License-Identifier: AGPL-3.0-only
"""Row mappers for note types and occlusion masks (no database needed)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from flashcards.adapters.db import (
    note_from_row,
    note_to_row,
    note_type_from_row,
    note_type_to_row,
    occlusion_from_json,
)
from flashcards.domain.note_types import CardTemplate, CustomNoteType
from flashcards.domain.notes import Mask, Note, NoteType, Occlusion

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def test_note_types_round_trip() -> None:
    vocab = CustomNoteType(
        id="t", owner_id="a", name="Vocab", fields=("Word", "Meaning"),
        templates=(CardTemplate(ordinal=3, name="Fwd", front="{{Word}}", back="{{Meaning}}"),),
        created_at=NOW,
    )
    assert note_type_from_row(note_type_to_row(vocab)) == vocab


def test_notes_round_trip_their_type_and_masks() -> None:
    occluded = Note(
        id="n", owner_id="a", deck_id="d", note_type=NoteType.IMAGE_OCCLUSION,
        fields=(("header", "Heart"),), created_at=NOW,
        occlusion=Occlusion(image_id="img", masks=(Mask(2, 0.1, 0.2, 0.3, 0.4),)),
    )
    assert note_from_row(note_to_row(occluded)) == occluded
    custom = Note(
        id="m", owner_id="a", deck_id="d", note_type=NoteType.CUSTOM,
        fields=(("Word", "chat"),), created_at=NOW, note_type_id="t",
    )
    assert note_from_row(note_to_row(custom)) == custom


def test_integer_coordinates_are_read_as_floats() -> None:
    occlusion = occlusion_from_json({"image_id": "img", "masks": [[1, 0, 0, 1, 1]]})
    assert occlusion.masks == (Mask(1, 0.0, 0.0, 1.0, 1.0),)


@pytest.mark.parametrize(
    "data",
    [
        {"image_id": "img", "masks": "nope"},
        {"image_id": "img", "masks": ["nope"]},
        {"image_id": "img", "masks": [[1, True, 0, 1, 1]]},
        {"image_id": "img", "masks": [[1, "0", 0, 1, 1]]},
    ],
)
def test_corrupt_occlusion_data_is_refused(data: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="corrupt occlusion"):
        occlusion_from_json(data)
