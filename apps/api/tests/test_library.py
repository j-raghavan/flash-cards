# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryNoteRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
)
from flashcards.application.deck_service import DeckService
from flashcards.application.library_service import LibraryService
from flashcards.application.note_service import NoteService
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.notes import NoteNotFound, NoteType
from flashcards.domain.scheduling import CardSchedule, Rating, ReviewLog, ScheduleState

START = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
ALICE = "user-alice"
BOB = "user-bob"


class FakeClock:
    def now(self) -> datetime:
        return START


class _Fixture:
    def __init__(self) -> None:
        self.deck_repo = InMemoryDeckRepository()
        self.note_repo = InMemoryNoteRepository()
        self.card_repo = InMemoryCardRepository()
        self.schedule_repo = InMemoryScheduleRepository()
        self.log_repo = InMemoryReviewLogRepository()
        clock = FakeClock()
        self.decks = DeckService(self.deck_repo, clock)
        self.notes = NoteService(self.decks, self.note_repo, self.card_repo, clock)
        self.library = LibraryService(
            self.decks,
            self.notes,
            self.deck_repo,
            self.note_repo,
            self.card_repo,
            self.schedule_repo,
            self.log_repo,
        )

    async def seed_card(self, card_id: str) -> None:
        await self.schedule_repo.upsert(
            CardSchedule(
                card_id=card_id,
                state=ScheduleState.LEARNING,
                step=0,
                stability=None,
                difficulty=None,
                due_at=START,
                last_reviewed_at=None,
            )
        )
        await self.log_repo.append(
            ReviewLog(
                id=f"log-{card_id}",
                owner_id=ALICE,
                card_id=card_id,
                rating=Rating.GOOD,
                reviewed_at=START,
                idempotency_key=None,
            )
        )


@pytest.fixture
def fx() -> _Fixture:
    return _Fixture()


async def test_delete_deck_cascades_to_all_content(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Deck")
    _note, cards = await fx.notes.create_note(
        owner_id=ALICE,
        deck_id=deck.id,
        note_type=NoteType.CLOZE,
        fields={"text": "{{c1::a}} {{c2::b}}"},
    )
    for card in cards:
        await fx.seed_card(card.id)

    await fx.library.delete_deck(owner_id=ALICE, deck_id=deck.id)

    assert await fx.deck_repo.get(deck.id) is None
    assert await fx.card_repo.list_for_deck(deck.id) == []
    for card in cards:
        assert await fx.schedule_repo.get(card.id) is None
    assert fx.log_repo._logs == []


async def test_delete_deck_cascades_subtree(fx: _Fixture) -> None:
    parent = await fx.decks.create(owner_id=ALICE, name="Parent")
    child = await fx.decks.create(owner_id=ALICE, name="Child", parent_id=parent.id)
    _note, cards = await fx.notes.create_note(
        owner_id=ALICE,
        deck_id=child.id,
        note_type=NoteType.BASIC,
        fields={"front": "Q", "back": "A"},
    )
    await fx.seed_card(cards[0].id)

    await fx.library.delete_deck(owner_id=ALICE, deck_id=parent.id)

    assert await fx.deck_repo.get(child.id) is None
    assert await fx.card_repo.get(cards[0].id) is None
    assert await fx.schedule_repo.get(cards[0].id) is None


async def test_delete_note_cascades_only_its_own_cards(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Deck")
    doomed, doomed_cards = await fx.notes.create_note(
        owner_id=ALICE,
        deck_id=deck.id,
        note_type=NoteType.CLOZE,
        fields={"text": "{{c1::a}} {{c2::b}}"},
    )
    _kept, kept_cards = await fx.notes.create_note(
        owner_id=ALICE,
        deck_id=deck.id,
        note_type=NoteType.BASIC,
        fields={"front": "Q", "back": "A"},
    )
    for card in [*doomed_cards, *kept_cards]:
        await fx.seed_card(card.id)

    await fx.library.delete_note(owner_id=ALICE, note_id=doomed.id)

    assert await fx.note_repo.get(doomed.id) is None
    for card in doomed_cards:
        assert await fx.card_repo.get(card.id) is None
        assert await fx.schedule_repo.get(card.id) is None
    # The other note's card and schedule survive.
    assert await fx.card_repo.get(kept_cards[0].id) is not None
    assert await fx.schedule_repo.get(kept_cards[0].id) is not None


async def test_delete_foreign_deck_is_not_found(fx: _Fixture) -> None:
    bob_deck = await fx.decks.create(owner_id=BOB, name="Bob's")
    with pytest.raises(DeckNotFound):
        await fx.library.delete_deck(owner_id=ALICE, deck_id=bob_deck.id)


async def test_delete_foreign_note_is_not_found(fx: _Fixture) -> None:
    bob_deck = await fx.decks.create(owner_id=BOB, name="Bob's")
    note, _ = await fx.notes.create_note(
        owner_id=BOB,
        deck_id=bob_deck.id,
        note_type=NoteType.BASIC,
        fields={"front": "Q", "back": "A"},
    )
    with pytest.raises(NoteNotFound):
        await fx.library.delete_note(owner_id=ALICE, note_id=note.id)
