# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.fsrs_scheduler import FsrsScheduler
from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryNoteRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
)
from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.application.review_service import ReviewService
from flashcards.domain.notes import CardNotFound, NoteType
from flashcards.domain.scheduling import CardSchedule, Rating, ScheduleState

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


# --- FSRS adapter (real library) ---


def test_fsrs_new_schedule_is_learning_due_now() -> None:
    schedule = FsrsScheduler().new_schedule("card-1", START)
    assert schedule.state is ScheduleState.LEARNING
    assert schedule.due_at == START


def test_fsrs_good_pushes_due_out_and_again_keeps_it_near() -> None:
    scheduler = FsrsScheduler()
    fresh = scheduler.new_schedule("card-1", START)
    good = scheduler.review(fresh, Rating.GOOD, START)
    again = scheduler.review(fresh, Rating.AGAIN, START)
    assert good.due_at > START
    assert again.due_at >= START
    assert good.due_at > again.due_at


def test_fsrs_reaches_review_state_after_two_goods() -> None:
    scheduler = FsrsScheduler()
    s = scheduler.new_schedule("card-1", START)
    s = scheduler.review(s, Rating.GOOD, START)
    s = scheduler.review(s, Rating.GOOD, s.due_at)
    assert s.state is ScheduleState.REVIEW
    assert s.stability is not None and s.stability > 0


# --- ReviewService (fake deterministic scheduler) ---


class FakeScheduler:
    def new_schedule(self, card_id: str, now: datetime) -> CardSchedule:
        return CardSchedule(
            card_id=card_id,
            state=ScheduleState.LEARNING,
            step=0,
            stability=None,
            difficulty=None,
            due_at=now,
            last_reviewed_at=None,
        )

    def review(self, schedule: CardSchedule, rating: Rating, now: datetime) -> CardSchedule:
        bump = timedelta(days=1) if rating >= Rating.GOOD else timedelta(minutes=1)
        return CardSchedule(
            card_id=schedule.card_id,
            state=ScheduleState.REVIEW,
            step=None,
            stability=10.0,
            difficulty=5.0,
            due_at=now + bump,
            last_reviewed_at=now,
        )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def notes(clock: FakeClock) -> NoteService:
    decks = DeckService(InMemoryDeckRepository(), clock)
    return NoteService(decks, InMemoryNoteRepository(), InMemoryCardRepository(), clock)


@pytest.fixture
def schedules() -> InMemoryScheduleRepository:
    return InMemoryScheduleRepository()


@pytest.fixture
def logs() -> InMemoryReviewLogRepository:
    return InMemoryReviewLogRepository()


@pytest.fixture
def reviews(
    notes: NoteService,
    schedules: InMemoryScheduleRepository,
    logs: InMemoryReviewLogRepository,
    clock: FakeClock,
) -> ReviewService:
    return ReviewService(notes, schedules, logs, FakeScheduler(), clock)


async def _card(notes: NoteService, owner: str = ALICE) -> tuple[str, str]:
    deck = await notes._decks.create(owner_id=owner, name="Deck")
    _, cards = await notes.create_note(
        owner_id=owner,
        deck_id=deck.id,
        note_type=NoteType.BASIC,
        fields={"front": "Q", "back": "A"},
    )
    return deck.id, cards[0].id


async def test_submit_advances_schedule_and_logs(
    reviews: ReviewService, notes: NoteService, schedules: InMemoryScheduleRepository
) -> None:
    _, card_id = await _card(notes)
    updated = await reviews.submit(owner_id=ALICE, card_id=card_id, rating=Rating.GOOD)
    assert updated.state is ScheduleState.REVIEW
    assert updated.due_at == START + timedelta(days=1)
    assert (await schedules.get(card_id)) is not None


async def test_submit_on_foreign_card_is_not_found(
    reviews: ReviewService, notes: NoteService
) -> None:
    _, bob_card = await _card(notes, owner=BOB)
    with pytest.raises(CardNotFound):
        await reviews.submit(owner_id=ALICE, card_id=bob_card, rating=Rating.GOOD)


async def test_submit_is_idempotent_on_key(
    reviews: ReviewService, notes: NoteService, logs: InMemoryReviewLogRepository
) -> None:
    _, card_id = await _card(notes)
    first = await reviews.submit(
        owner_id=ALICE, card_id=card_id, rating=Rating.GOOD, idempotency_key="k1"
    )
    second = await reviews.submit(
        owner_id=ALICE, card_id=card_id, rating=Rating.AGAIN, idempotency_key="k1"
    )
    # The replay is a no-op: same schedule, and only one log recorded.
    assert second.due_at == first.due_at
    assert len(logs._logs) == 1


async def test_due_queue_lists_new_cards(reviews: ReviewService, notes: NoteService) -> None:
    deck_id, card_id = await _card(notes)
    due = await reviews.due_queue(owner_id=ALICE, deck_id=deck_id)
    assert [c.id for c in due] == [card_id]


async def test_due_queue_excludes_suspended_and_not_due(
    reviews: ReviewService, notes: NoteService, clock: FakeClock
) -> None:
    deck_id, card_id = await _card(notes)
    # Review it so it's scheduled a day out, then it should drop out of today's queue.
    await reviews.submit(owner_id=ALICE, card_id=card_id, rating=Rating.GOOD)
    assert await reviews.due_queue(owner_id=ALICE, deck_id=deck_id) == []
    # When its due date arrives it returns.
    clock.advance(timedelta(days=1))
    assert [c.id for c in await reviews.due_queue(owner_id=ALICE, deck_id=deck_id)] == [card_id]
    # Suspended cards never appear.
    await notes.suspend_card(owner_id=ALICE, card_id=card_id)
    assert await reviews.due_queue(owner_id=ALICE, deck_id=deck_id) == []


def test_rating_values_map_to_fsrs_scale() -> None:
    assert (Rating.AGAIN, Rating.HARD, Rating.GOOD, Rating.EASY) == (1, 2, 3, 4)
