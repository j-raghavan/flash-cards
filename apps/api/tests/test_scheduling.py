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


def test_fsrs_again_on_a_review_card_relearns() -> None:
    scheduler = FsrsScheduler()
    s = scheduler.new_schedule("card-1", START)
    s = scheduler.review(s, Rating.GOOD, START)
    s = scheduler.review(s, Rating.GOOD, s.due_at)
    assert s.state is ScheduleState.REVIEW
    s = scheduler.review(s, Rating.AGAIN, s.due_at)
    assert s.state is ScheduleState.RELEARNING


def test_fsrs_easy_schedules_further_out_than_hard() -> None:
    scheduler = FsrsScheduler()
    fresh = scheduler.new_schedule("card-1", START)
    hard = scheduler.review(fresh, Rating.HARD, START)
    easy = scheduler.review(fresh, Rating.EASY, START)
    assert easy.due_at > hard.due_at


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
    outcome = await reviews.submit(owner_id=ALICE, card_id=card_id, rating=Rating.GOOD)
    updated = outcome.schedule
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
    # The replay is a no-op: same schedule and review, and only one log recorded.
    assert second.schedule.due_at == first.schedule.due_at
    assert second.review_id == first.review_id
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


async def test_same_key_on_different_cards_both_apply(
    reviews: ReviewService, notes: NoteService, logs: InMemoryReviewLogRepository
) -> None:
    deck_id, card_a = await _card(notes)
    _, cards_b = await notes.create_note(
        owner_id=ALICE,
        deck_id=deck_id,
        note_type=NoteType.BASIC,
        fields={"front": "Q2", "back": "A2"},
    )
    await reviews.submit(owner_id=ALICE, card_id=card_a, rating=Rating.GOOD, idempotency_key="k")
    await reviews.submit(
        owner_id=ALICE, card_id=cards_b[0].id, rating=Rating.GOOD, idempotency_key="k"
    )
    # Same key, different cards: both reviews are recorded (not a false dedup).
    assert len(logs._logs) == 2


async def test_same_key_across_owners_both_apply(
    reviews: ReviewService, notes: NoteService, logs: InMemoryReviewLogRepository
) -> None:
    _, alice_card = await _card(notes, owner=ALICE)
    _, bob_card = await _card(notes, owner=BOB)
    await reviews.submit(
        owner_id=ALICE, card_id=alice_card, rating=Rating.GOOD, idempotency_key="k"
    )
    await reviews.submit(owner_id=BOB, card_id=bob_card, rating=Rating.GOOD, idempotency_key="k")
    assert len(logs._logs) == 2


async def test_deck_stats_counts_each_card_state(
    reviews: ReviewService,
    notes: NoteService,
    schedules: InMemoryScheduleRepository,
) -> None:
    deck = await notes._decks.create(owner_id=ALICE, name="D")

    async def make_card(front: str) -> str:
        _, cards = await notes.create_note(
            owner_id=ALICE,
            deck_id=deck.id,
            note_type=NoteType.BASIC,
            fields={"front": front, "back": "A"},
        )
        return cards[0].id

    _c_new = await make_card("new")
    c_learning = await make_card("learning")
    c_review = await make_card("review")
    c_mature = await make_card("mature")
    c_suspended = await make_card("suspended")

    await schedules.upsert(
        CardSchedule(
            card_id=c_learning,
            state=ScheduleState.LEARNING,
            step=0,
            stability=None,
            difficulty=None,
            due_at=START,
            last_reviewed_at=None,
        )
    )
    await schedules.upsert(
        CardSchedule(
            card_id=c_review,
            state=ScheduleState.REVIEW,
            step=None,
            stability=5.0,
            difficulty=5.0,
            due_at=START + timedelta(days=5),
            last_reviewed_at=START,
        )
    )
    await schedules.upsert(
        CardSchedule(
            card_id=c_mature,
            state=ScheduleState.REVIEW,
            step=None,
            stability=50.0,
            difficulty=5.0,
            due_at=START,
            last_reviewed_at=START - timedelta(days=30),
        )
    )
    await notes.suspend_card(owner_id=ALICE, card_id=c_suspended)

    stats = await reviews.deck_stats(owner_id=ALICE, deck_id=deck.id)
    assert stats.total == 5
    assert stats.new == 1
    assert stats.learning == 1
    assert stats.review == 1
    assert stats.mature == 1
    assert stats.suspended == 1
    # Due today: new + learning + mature (the review card is due in 5 days).
    assert stats.due == 3
    # (review + mature) / (total - suspended) = 2 / 4
    assert stats.proficiency == 50


async def test_deck_stats_proficiency_zero_when_all_suspended(
    reviews: ReviewService, notes: NoteService
) -> None:
    deck = await notes._decks.create(owner_id=ALICE, name="D")
    _, cards = await notes.create_note(
        owner_id=ALICE,
        deck_id=deck.id,
        note_type=NoteType.BASIC,
        fields={"front": "Q", "back": "A"},
    )
    await notes.suspend_card(owner_id=ALICE, card_id=cards[0].id)
    stats = await reviews.deck_stats(owner_id=ALICE, deck_id=deck.id)
    assert stats.proficiency == 0
    assert stats.suspended == 1
