# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryNoteRepository,
)
from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.notes import (
    MAX_FIELD_LEN,
    CardNotFound,
    InvalidNote,
    Note,
    NoteNotFound,
    NoteType,
    card_ordinals,
    parse_cloze_ordinals,
)

START = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
ALICE = "user-alice"
BOB = "user-bob"


class FakeClock:
    def __init__(self, now: datetime = START) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


# --- domain ---


def test_parse_cloze_dedups_and_sorts() -> None:
    assert parse_cloze_ordinals("{{c2::x}} {{c1::y}} {{c1::z}}") == [1, 2]


def test_parse_cloze_requires_a_deletion() -> None:
    with pytest.raises(InvalidNote):
        parse_cloze_ordinals("no deletions here")


def test_card_ordinals_basic_requires_both_fields() -> None:
    assert card_ordinals(NoteType.BASIC, (("front", "Q"), ("back", "A"))) == [0]
    with pytest.raises(InvalidNote):
        card_ordinals(NoteType.BASIC, (("front", "Q"), ("back", "  ")))


def test_card_ordinals_rejects_overlong_field() -> None:
    with pytest.raises(InvalidNote):
        card_ordinals(NoteType.BASIC, (("front", "x" * (MAX_FIELD_LEN + 1)), ("back", "A")))


def test_card_ordinals_rejects_overlong_extra_field() -> None:
    # A field the note type does not read is still bounded.
    with pytest.raises(InvalidNote):
        card_ordinals(
            NoteType.BASIC,
            (("front", "Q"), ("back", "A"), ("extra", "x" * (MAX_FIELD_LEN + 1))),
        )


def test_card_ordinals_rejects_too_many_fields() -> None:
    fields = tuple((f"f{i}", "v") for i in range(65))
    with pytest.raises(InvalidNote):
        card_ordinals(NoteType.CLOZE, fields)


def test_cloze_c0_is_not_a_valid_deletion() -> None:
    with pytest.raises(InvalidNote):
        parse_cloze_ordinals("{{c0::nope}}")


def test_cloze_requires_closing_braces() -> None:
    with pytest.raises(InvalidNote):
        parse_cloze_ordinals("{{c1::unterminated")


def test_cloze_folds_leading_zero_to_same_ordinal() -> None:
    assert parse_cloze_ordinals("{{c01::a}} {{c1::b}}") == [1]


def test_note_field_accessor() -> None:
    note = Note(
        id="n1",
        owner_id=ALICE,
        deck_id="d1",
        note_type=NoteType.BASIC,
        fields=(("front", "Q"), ("back", "A")),
        created_at=START,
    )
    assert note.field("front") == "Q"
    assert note.field("missing") is None


# --- service ---


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def decks(clock: FakeClock) -> DeckService:
    return DeckService(InMemoryDeckRepository(), clock)


@pytest.fixture
def cards_repo() -> InMemoryCardRepository:
    return InMemoryCardRepository()


@pytest.fixture
def notes_repo() -> InMemoryNoteRepository:
    return InMemoryNoteRepository()


@pytest.fixture
def service(
    decks: DeckService,
    notes_repo: InMemoryNoteRepository,
    cards_repo: InMemoryCardRepository,
    clock: FakeClock,
) -> NoteService:
    return NoteService(decks, notes_repo, cards_repo, clock)


async def _deck(decks: DeckService, owner: str = ALICE) -> str:
    deck = await decks.create(owner_id=owner, name="Deck")
    return deck.id


async def test_basic_note_makes_one_card(service: NoteService, decks: DeckService) -> None:
    deck_id = await _deck(decks)
    note, cards = await service.create_note(
        owner_id=ALICE,
        deck_id=deck_id,
        note_type=NoteType.BASIC,
        fields={"front": "Q", "back": "A"},
    )
    assert note.note_type is NoteType.BASIC
    assert [c.ordinal for c in cards] == [0]


async def test_cloze_note_makes_one_card_per_deletion(
    service: NoteService, decks: DeckService
) -> None:
    deck_id = await _deck(decks)
    _, cards = await service.create_note(
        owner_id=ALICE,
        deck_id=deck_id,
        note_type=NoteType.CLOZE,
        fields={"text": "The {{c1::sky}} is {{c2::blue}} and {{c1::high}}"},
    )
    assert sorted(c.ordinal for c in cards) == [1, 2]


async def test_create_in_missing_deck_is_not_found(service: NoteService) -> None:
    with pytest.raises(DeckNotFound):
        await service.create_note(
            owner_id=ALICE,
            deck_id="ghost",
            note_type=NoteType.BASIC,
            fields={"front": "Q", "back": "A"},
        )


async def test_create_in_foreign_deck_is_not_found(
    service: NoteService, decks: DeckService
) -> None:
    bob_deck = await _deck(decks, owner=BOB)
    with pytest.raises(DeckNotFound):
        await service.create_note(
            owner_id=ALICE,
            deck_id=bob_deck,
            note_type=NoteType.BASIC,
            fields={"front": "Q", "back": "A"},
        )


async def test_invalid_note_fields_rejected(service: NoteService, decks: DeckService) -> None:
    deck_id = await _deck(decks)
    with pytest.raises(InvalidNote):
        await service.create_note(
            owner_id=ALICE,
            deck_id=deck_id,
            note_type=NoteType.BASIC,
            fields={"front": "Q"},
        )


async def test_cloze_note_without_text_field_is_rejected(
    service: NoteService, decks: DeckService
) -> None:
    deck_id = await _deck(decks)
    with pytest.raises(InvalidNote):
        await service.create_note(
            owner_id=ALICE,
            deck_id=deck_id,
            note_type=NoteType.CLOZE,
            fields={"front": "Q", "back": "A"},
        )


async def test_list_cards_in_deck(service: NoteService, decks: DeckService) -> None:
    deck_id = await _deck(decks)
    await service.create_note(
        owner_id=ALICE,
        deck_id=deck_id,
        note_type=NoteType.CLOZE,
        fields={"text": "{{c1::a}} {{c2::b}}"},
    )
    cards = await service.list_cards(owner_id=ALICE, deck_id=deck_id)
    assert len(cards) == 2


async def test_list_cards_in_foreign_deck_is_not_found(
    service: NoteService, decks: DeckService
) -> None:
    bob_deck = await _deck(decks, owner=BOB)
    with pytest.raises(DeckNotFound):
        await service.list_cards(owner_id=ALICE, deck_id=bob_deck)


async def test_get_foreign_or_missing_card_is_not_found(
    service: NoteService, decks: DeckService
) -> None:
    bob_deck = await _deck(decks, owner=BOB)
    _, bob_cards = await service.create_note(
        owner_id=BOB,
        deck_id=bob_deck,
        note_type=NoteType.BASIC,
        fields={"front": "Q", "back": "A"},
    )
    with pytest.raises(CardNotFound):
        await service.get_card(owner_id=ALICE, card_id=bob_cards[0].id)
    with pytest.raises(CardNotFound):
        await service.get_card(owner_id=ALICE, card_id="ghost")


async def test_suspend_and_unsuspend(service: NoteService, decks: DeckService) -> None:
    deck_id = await _deck(decks)
    _, cards = await service.create_note(
        owner_id=ALICE,
        deck_id=deck_id,
        note_type=NoteType.BASIC,
        fields={"front": "Q", "back": "A"},
    )
    card_id = cards[0].id
    suspended = await service.suspend_card(owner_id=ALICE, card_id=card_id)
    assert suspended.is_suspended
    restored = await service.unsuspend_card(owner_id=ALICE, card_id=card_id)
    assert not restored.is_suspended


async def test_delete_note_removes_its_cards(
    service: NoteService, decks: DeckService
) -> None:
    deck_id = await _deck(decks)
    note, _ = await service.create_note(
        owner_id=ALICE,
        deck_id=deck_id,
        note_type=NoteType.CLOZE,
        fields={"text": "{{c1::a}} {{c2::b}}"},
    )
    await service.delete_note(owner_id=ALICE, note_id=note.id)
    assert await service.list_cards(owner_id=ALICE, deck_id=deck_id) == []


async def test_get_foreign_or_missing_note_is_not_found(
    service: NoteService, decks: DeckService
) -> None:
    bob_deck = await _deck(decks, owner=BOB)
    note, _ = await service.create_note(
        owner_id=BOB,
        deck_id=bob_deck,
        note_type=NoteType.BASIC,
        fields={"front": "Q", "back": "A"},
    )
    with pytest.raises(NoteNotFound):
        await service.get_note(owner_id=ALICE, note_id=note.id)
    with pytest.raises(NoteNotFound):
        await service.get_note(owner_id=ALICE, note_id="ghost")


async def test_delete_foreign_note_is_not_found(
    service: NoteService, decks: DeckService
) -> None:
    bob_deck = await _deck(decks, owner=BOB)
    note, _ = await service.create_note(
        owner_id=BOB,
        deck_id=bob_deck,
        note_type=NoteType.BASIC,
        fields={"front": "Q", "back": "A"},
    )
    with pytest.raises(NoteNotFound):
        await service.delete_note(owner_id=ALICE, note_id=note.id)
