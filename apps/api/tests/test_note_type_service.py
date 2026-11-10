# SPDX-License-Identifier: AGPL-3.0-only
"""Custom note types and image occlusion notes through the services (ADR 0013)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from flashcards.adapters.fsrs_scheduler import FsrsScheduler
from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryMediaRepository,
    InMemoryMockExamRepository,
    InMemoryNoteRepository,
    InMemoryNoteTypeRepository,
    InMemoryPassageRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
    InMemoryShareLinkRepository,
)
from flashcards.application.deck_service import DeckService
from flashcards.application.library_service import LibraryService
from flashcards.application.note_service import NoteService
from flashcards.application.note_type_service import NoteTypeService
from flashcards.application.review_service import ReviewService
from flashcards.domain.media import MediaAsset, MediaNotFound
from flashcards.domain.note_types import (
    MAX_NOTE_TYPES_PER_OWNER,
    CustomNoteType,
    InvalidNoteType,
    NoteTypeInUse,
    NoteTypeNotFound,
    TemplateSpec,
    TooManyNoteTypes,
)
from flashcards.domain.notes import InvalidNote, Mask, NoteType, Occlusion
from flashcards.domain.scheduling import Rating

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ALICE, BOB = "alice", "bob"
FORWARD = TemplateSpec("Forward", "{{Word}}", "{{FrontSide}}<hr>{{Meaning}}")
REVERSE = TemplateSpec("Reverse", "{{Meaning}}", "{{FrontSide}}<hr>{{Word}}")


class _Clock:
    def now(self) -> datetime:
        return NOW


def _masks(*ids: int) -> tuple[Mask, ...]:
    return tuple(
        Mask(id=i, left=0.05 * i, top=0.1, width=0.04, height=0.2) for i in ids
    )


class _Rig:
    def __init__(self) -> None:
        clock = _Clock()
        self.cards = InMemoryCardRepository()
        self.schedules = InMemoryScheduleRepository()
        self.logs = InMemoryReviewLogRepository()
        self.media = InMemoryMediaRepository()
        self.type_repo = InMemoryNoteTypeRepository()
        deck_repo, self.note_repo = InMemoryDeckRepository(), InMemoryNoteRepository()
        self.decks = DeckService(deck_repo, clock)
        self.notes = NoteService(self.decks, self.note_repo, self.cards, clock)
        self.reviews = ReviewService(self.notes, self.schedules, self.logs, FsrsScheduler(), clock)
        self.library = LibraryService(
            self.decks, self.notes, deck_repo, self.note_repo, self.cards, self.schedules,
            self.logs, InMemoryPassageRepository(), InMemoryMockExamRepository(),
            InMemoryShareLinkRepository(),
        )
        self.service = NoteTypeService(
            self.type_repo, self.note_repo, self.notes, self.library, self.media, clock
        )

    async def vocab(self, owner: str = ALICE) -> CustomNoteType:
        return await self.service.create(
            owner_id=owner, name="Vocabulary", fields=["Word", "Meaning"],
            templates=[FORWARD, REVERSE],
        )

    async def deck(self, owner: str = ALICE) -> str:
        return (await self.decks.create(owner_id=owner, name="Lang")).id

    async def image(self, owner: str = ALICE, content_type: str = "image/png") -> str:
        asset = MediaAsset(
            id=f"{owner}-{content_type}", owner_id=owner, content_type=content_type,
            data=b"bytes", created_at=NOW,
        )
        await self.media.add(asset)
        return asset.id


async def test_note_types_are_owner_scoped() -> None:
    rig = _Rig()
    vocab = await rig.vocab()
    assert await rig.service.get(owner_id=ALICE, note_type_id=vocab.id) == vocab
    assert await rig.service.list_types(owner_id=ALICE) == [vocab]
    assert await rig.service.list_types(owner_id=BOB) == []
    with pytest.raises(NoteTypeNotFound):
        await rig.service.get(owner_id=BOB, note_type_id=vocab.id)
    with pytest.raises(NoteTypeNotFound):
        await rig.service.update(owner_id=BOB, note_type_id=vocab.id, name="Mine")
    with pytest.raises(NoteTypeNotFound):
        await rig.service.delete(owner_id=BOB, note_type_id="missing")


async def test_updating_a_missing_type_is_a_no_op() -> None:
    rig = _Rig()
    vocab = await rig.vocab()
    await rig.type_repo.delete(vocab.id)
    await rig.type_repo.update(vocab)
    assert await rig.type_repo.get(vocab.id) is None


async def test_the_number_of_note_types_is_capped() -> None:
    rig = _Rig()
    for _ in range(MAX_NOTE_TYPES_PER_OWNER):
        await rig.vocab()
    with pytest.raises(TooManyNoteTypes):
        await rig.vocab()


async def test_a_custom_note_gets_a_card_per_template_with_content() -> None:
    rig = _Rig()
    vocab, deck = await rig.vocab(), await rig.deck()
    note, cards = await rig.service.create_note(
        owner_id=ALICE, deck_id=deck, note_type=NoteType.CUSTOM,
        fields={"Word": "chat"}, note_type_id=vocab.id,
    )
    assert note.note_type_id == vocab.id
    assert note.fields == (("Word", "chat"), ("Meaning", ""))
    assert [c.ordinal for c in cards] == [0]  # Reverse's front is empty


async def test_custom_notes_need_a_type_of_their_own() -> None:
    rig = _Rig()
    vocab, deck = await rig.vocab(BOB), await rig.deck()
    with pytest.raises(NoteTypeNotFound):
        await rig.service.create_note(
            owner_id=ALICE, deck_id=deck, note_type=NoteType.CUSTOM,
            fields={"Word": "x"}, note_type_id=vocab.id,
        )
    with pytest.raises(NoteTypeNotFound):  # also refused when called directly
        await rig.notes.create_note(
            owner_id=ALICE, deck_id=deck, note_type=NoteType.CUSTOM,
            fields={"Word": "x"}, custom=vocab,
        )
    with pytest.raises(InvalidNote, match="only a custom note"):
        await rig.service.create_note(
            owner_id=ALICE, deck_id=deck, note_type=NoteType.BASIC,
            fields={"front": "q", "back": "a"}, note_type_id=vocab.id,
        )
    with pytest.raises(InvalidNote, match="needs its note type"):
        await rig.service.create_note(
            owner_id=ALICE, deck_id=deck, note_type=NoteType.CUSTOM, fields={"Word": "x"}
        )


async def test_editing_a_type_rederives_cards_and_keeps_surviving_schedules() -> None:
    rig = _Rig()
    vocab, deck = await rig.vocab(), await rig.deck()
    note, cards = await rig.service.create_note(
        owner_id=ALICE, deck_id=deck, note_type=NoteType.CUSTOM,
        fields={"Word": "chat", "Meaning": "cat"}, note_type_id=vocab.id,
    )
    before = {c.ordinal: c.id for c in cards}
    for card_id in before.values():
        await rig.reviews.submit(owner_id=ALICE, card_id=card_id, rating=Rating.GOOD)

    edited = await rig.service.update(
        owner_id=ALICE, note_type_id=vocab.id, fields=["Term", "Meaning"],
        renamed={"Word": "Term"},
        templates=[
            TemplateSpec("Forward", "{{Term}}", "{{Meaning}}", ordinal=0),
            TemplateSpec("Spelling", "{{Meaning}}", "{{Term}}"),
        ],
    )

    assert edited.fields == ("Term", "Meaning")
    after = {c.ordinal: c.id for c in await rig.cards.list_for_notes([note.id])}
    assert set(after) == {0, 2} and after[0] == before[0]  # Forward kept, Reverse gone
    assert await rig.schedules.get(ALICE, before[0]) is not None
    assert await rig.schedules.get(ALICE, before[1]) is None  # purged with its card
    assert {log.card_id for log in await rig.logs.list_for_owner(ALICE)} == {before[0]}
    stored = await rig.note_repo.get(note.id)
    assert stored is not None and stored.fields == (("Term", "chat"), ("Meaning", "cat"))
    assert await rig.type_repo.get(vocab.id) == edited


async def test_a_type_edit_that_would_leave_a_note_without_cards_changes_nothing() -> None:
    rig = _Rig()
    vocab, deck = await rig.vocab(), await rig.deck()
    note, _cards = await rig.service.create_note(
        owner_id=ALICE, deck_id=deck, note_type=NoteType.CUSTOM,
        fields={"Word": "chat"}, note_type_id=vocab.id,
    )
    with pytest.raises(InvalidNoteType, match="without any card"):
        await rig.service.update(
            owner_id=ALICE, note_type_id=vocab.id,
            templates=[TemplateSpec("Reverse", "{{Meaning}}", "{{Word}}", ordinal=1)],
        )
    assert await rig.type_repo.get(vocab.id) == vocab
    assert [c.ordinal for c in await rig.cards.list_for_notes([note.id])] == [0]


async def test_a_type_in_use_cannot_be_deleted() -> None:
    rig = _Rig()
    vocab, deck = await rig.vocab(), await rig.deck()
    note, _cards = await rig.service.create_note(
        owner_id=ALICE, deck_id=deck, note_type=NoteType.CUSTOM,
        fields={"Word": "chat"}, note_type_id=vocab.id,
    )
    with pytest.raises(NoteTypeInUse):
        await rig.service.delete(owner_id=ALICE, note_type_id=vocab.id)
    await rig.library.delete_note(owner_id=ALICE, note_id=note.id)
    await rig.service.delete(owner_id=ALICE, note_type_id=vocab.id)
    assert await rig.type_repo.get(vocab.id) is None


async def test_editing_a_custom_note_uses_its_type() -> None:
    rig = _Rig()
    vocab, deck = await rig.vocab(), await rig.deck()
    note, _cards = await rig.service.create_note(
        owner_id=ALICE, deck_id=deck, note_type=NoteType.CUSTOM,
        fields={"Word": "chat"}, note_type_id=vocab.id,
    )
    updated, cards = await rig.service.update_note(
        owner_id=ALICE, note_id=note.id, fields={"Word": "chat", "Meaning": "cat"}
    )
    assert updated.fields == (("Word", "chat"), ("Meaning", "cat"))
    assert [c.ordinal for c in cards] == [0, 1]
    tagged, _cards = await rig.service.update_note(owner_id=ALICE, note_id=note.id, tags=["fr"])
    assert tagged.tags == ("fr",) and tagged.fields == updated.fields
    with pytest.raises(InvalidNote, match="does not define"):
        await rig.service.update_note(owner_id=ALICE, note_id=note.id, fields={"Nope": "x"})


async def test_a_note_cannot_be_edited_with_another_type() -> None:
    rig = _Rig()
    vocab, other, deck = await rig.vocab(), await rig.vocab(), await rig.deck()
    note, _cards = await rig.service.create_note(
        owner_id=ALICE, deck_id=deck, note_type=NoteType.CUSTOM,
        fields={"Word": "chat"}, note_type_id=vocab.id,
    )
    with pytest.raises(InvalidNote, match="not this note's type"):
        await rig.library.update_note(owner_id=ALICE, note_id=note.id, custom=other)


async def test_an_occlusion_note_makes_a_card_per_mask_id() -> None:
    rig = _Rig()
    deck, image = await rig.deck(), await rig.image()
    note, cards = await rig.service.create_note(
        owner_id=ALICE, deck_id=deck, note_type=NoteType.IMAGE_OCCLUSION,
        fields={"header": "Heart"}, occlusion=Occlusion(image_id=image, masks=_masks(1, 2, 2)),
    )
    assert note.occlusion is not None and note.occlusion.image_id == image
    assert [c.ordinal for c in cards] == [1, 2]


async def test_the_occlusion_image_must_be_the_owners_image() -> None:
    rig = _Rig()
    deck = await rig.deck()
    for image, error in [
        (await rig.image(BOB), MediaNotFound),
        ("missing", MediaNotFound),
        (await rig.image(ALICE, "audio/mpeg"), InvalidNote),
    ]:
        with pytest.raises(error):
            await rig.service.create_note(
                owner_id=ALICE, deck_id=deck, note_type=NoteType.IMAGE_OCCLUSION, fields={},
                occlusion=Occlusion(image_id=image, masks=_masks(1)),
            )


async def test_editing_masks_keeps_cards_whose_mask_id_survives() -> None:
    rig = _Rig()
    deck, image = await rig.deck(), await rig.image()
    note, cards = await rig.service.create_note(
        owner_id=ALICE, deck_id=deck, note_type=NoteType.IMAGE_OCCLUSION, fields={},
        occlusion=Occlusion(image_id=image, masks=_masks(1, 2)),
    )
    before = {c.ordinal: c.id for c in cards}
    await rig.reviews.submit(owner_id=ALICE, card_id=before[1], rating=Rating.GOOD)

    moved = Occlusion(image_id=image, masks=(*_masks(3), Mask(1, 0.5, 0.5, 0.1, 0.1)))
    updated, after_cards = await rig.service.update_note(
        owner_id=ALICE, note_id=note.id, occlusion=moved
    )

    after = {c.ordinal: c.id for c in after_cards}
    assert updated.occlusion == moved
    assert set(after) == {1, 3} and after[1] == before[1]  # mask 1 moved, kept its card
    assert await rig.schedules.get(ALICE, before[1]) is not None


async def test_changing_the_image_checks_the_new_one() -> None:
    rig = _Rig()
    deck, image = await rig.deck(), await rig.image()
    note, _cards = await rig.service.create_note(
        owner_id=ALICE, deck_id=deck, note_type=NoteType.IMAGE_OCCLUSION, fields={},
        occlusion=Occlusion(image_id=image, masks=_masks(1)),
    )
    with pytest.raises(MediaNotFound):
        await rig.service.update_note(
            owner_id=ALICE, note_id=note.id,
            occlusion=Occlusion(image_id=await rig.image(BOB), masks=_masks(1)),
        )
    other = await rig.image(ALICE, "image/jpeg")
    updated, _cards = await rig.service.update_note(
        owner_id=ALICE, note_id=note.id, occlusion=Occlusion(image_id=other, masks=_masks(1)),
    )
    assert updated.occlusion is not None and updated.occlusion.image_id == other


async def test_only_occlusion_notes_take_masks() -> None:
    rig = _Rig()
    deck, image = await rig.deck(), await rig.image()
    note, _cards = await rig.notes.create_note(
        owner_id=ALICE, deck_id=deck, note_type=NoteType.BASIC,
        fields={"front": "q", "back": "a"},
    )
    with pytest.raises(InvalidNote, match="only an image occlusion note"):
        await rig.service.update_note(
            owner_id=ALICE, note_id=note.id, occlusion=Occlusion(image_id=image, masks=_masks(1))
        )
