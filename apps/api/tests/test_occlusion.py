# SPDX-License-Identifier: AGPL-3.0-only
"""Image occlusion masks and how note types pick their cards (ADR 0013)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from flashcards.domain.note_types import TemplateSpec, new_note_type
from flashcards.domain.notes import (
    MAX_MASK_ID,
    MAX_MASKS,
    InvalidNote,
    Mask,
    Note,
    NoteType,
    Occlusion,
    card_ordinals,
    note_fields,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def _mask(mask_id: int = 1, left: float = 0.1, top: float = 0.1, width: float = 0.2,
          height: float = 0.2) -> Mask:
    return Mask(id=mask_id, left=left, top=top, width=width, height=height)


def test_masks_sharing_an_id_make_one_card() -> None:
    occlusion = Occlusion(image_id="img", masks=(_mask(2), _mask(1), _mask(2, left=0.5)))
    assert occlusion.ordinals() == [1, 2]
    fields = (("header", "Heart"), ("back_extra", ""))
    assert card_ordinals(NoteType.IMAGE_OCCLUSION, fields, occlusion=occlusion) == [1, 2]


def test_a_mask_may_touch_the_far_edges() -> None:
    edge = _mask(left=0.1, top=0.7, width=0.9, height=0.3)  # sums land a hair over 1
    assert Occlusion(image_id="img", masks=(edge,)).ordinals() == [1]


@pytest.mark.parametrize(
    ("occlusion", "message"),
    [
        (Occlusion(image_id="", masks=(_mask(),)), "needs an image"),
        (Occlusion(image_id="x" * 65, masks=(_mask(),)), "needs an image"),
        (Occlusion(image_id="img", masks=()), "1 to"),
        (Occlusion(image_id="img", masks=tuple(_mask() for _ in range(MAX_MASKS + 1))), "1 to"),
        (Occlusion(image_id="img", masks=(_mask(0),)), "mask ids"),
        (Occlusion(image_id="img", masks=(_mask(MAX_MASK_ID + 1),)), "mask ids"),
        (Occlusion(image_id="img", masks=(_mask(left=float("nan")),)), "finite"),
        (Occlusion(image_id="img", masks=(_mask(width=float("inf")),)), "finite"),
        (Occlusion(image_id="img", masks=(_mask(left=-0.1),)), "non-negative"),
        (Occlusion(image_id="img", masks=(_mask(top=-0.1),)), "non-negative"),
        (Occlusion(image_id="img", masks=(_mask(width=0),)), "positive size"),
        (Occlusion(image_id="img", masks=(_mask(height=0),)), "positive size"),
        (Occlusion(image_id="img", masks=(_mask(left=0.9, width=0.2),)), "within the image"),
        (Occlusion(image_id="img", masks=(_mask(top=0.9, height=0.2),)), "within the image"),
    ],
)
def test_masks_are_validated(occlusion: Occlusion, message: str) -> None:
    with pytest.raises(InvalidNote, match=message):
        occlusion.ordinals()


def test_occlusion_notes_have_only_their_own_fields() -> None:
    occlusion = Occlusion(image_id="img", masks=(_mask(),))
    with pytest.raises(InvalidNote, match="header and back_extra"):
        card_ordinals(NoteType.IMAGE_OCCLUSION, (("front", "x"),), occlusion=occlusion)


def test_each_type_needs_exactly_its_own_extras() -> None:
    occlusion = Occlusion(image_id="img", masks=(_mask(),))
    vocab = new_note_type(
        id="t", owner_id="a", name="V", fields=["Word"],
        templates=[TemplateSpec("Card", "{{Word}}", "")], created_at=NOW,
    )
    basic = (("front", "q"), ("back", "a"))
    with pytest.raises(InvalidNote, match="needs its note type"):
        card_ordinals(NoteType.CUSTOM, (("Word", "x"),))
    with pytest.raises(InvalidNote, match="only a custom note"):
        card_ordinals(NoteType.BASIC, basic, custom=vocab)
    with pytest.raises(InvalidNote, match="needs an image and masks"):
        card_ordinals(NoteType.IMAGE_OCCLUSION, ())
    with pytest.raises(InvalidNote, match="only an image occlusion note"):
        card_ordinals(NoteType.BASIC, basic, occlusion=occlusion)
    assert card_ordinals(NoteType.BASIC, basic) == [0]


def test_a_note_can_take_new_masks() -> None:
    note = Note(
        id="n", owner_id="a", deck_id="d", note_type=NoteType.IMAGE_OCCLUSION, fields=(),
        created_at=NOW, occlusion=Occlusion(image_id="img", masks=(_mask(),)),
    )
    moved = Occlusion(image_id="img", masks=(_mask(3),))
    assert note.with_occlusion(moved).occlusion == moved


def test_occlusion_fields_are_stored_in_a_fixed_order() -> None:
    given = {"back_extra": "valve", "header": "Heart"}
    assert note_fields(NoteType.IMAGE_OCCLUSION, given) == (
        ("header", "Heart"), ("back_extra", "valve"),
    )
    assert note_fields(NoteType.IMAGE_OCCLUSION, {}) == (("header", ""), ("back_extra", ""))
    assert note_fields(NoteType.BASIC, {"back": "a", "front": "q"}) == (
        ("back", "a"), ("front", "q"),
    )
    with pytest.raises(InvalidNote, match="header and back_extra"):
        note_fields(NoteType.IMAGE_OCCLUSION, {"front": "x"})
