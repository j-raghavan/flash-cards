# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryGoalRepository,
    InMemoryNoteRepository,
    InMemoryScheduleRepository,
)
from flashcards.application.deck_service import DeckService
from flashcards.application.goal_service import GoalService
from flashcards.application.note_service import NoteService
from flashcards.application.planner_service import PlannerService
from flashcards.domain.goals import GoalNotFound
from flashcards.domain.notes import Card, NoteType
from flashcards.domain.scheduling import CardSchedule, ScheduleState

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
EXAM = NOW + timedelta(days=30)
ALICE = "alice"
BOB = "bob"


class FakeClock:
    def now(self) -> datetime:
        return NOW


class _Fixture:
    def __init__(self) -> None:
        clock = FakeClock()
        self.decks = DeckService(InMemoryDeckRepository(), clock)
        self.cards = InMemoryCardRepository()
        self.schedules = InMemoryScheduleRepository()
        self.notes = NoteService(self.decks, InMemoryNoteRepository(), self.cards, clock)
        self.goals = GoalService(self.decks, InMemoryGoalRepository(), clock)
        self.planner = PlannerService(self.goals, self.cards, self.schedules, clock)

    async def add_card(self, deck_id: str) -> Card:
        _note, cards = await self.notes.create_note(
            owner_id=ALICE,
            deck_id=deck_id,
            note_type=NoteType.BASIC,
            fields={"front": "q", "back": "a"},
        )
        return cards[0]

    async def schedule(self, card_id: str, stability: float) -> None:
        await self.schedules.upsert(
            CardSchedule(
                card_id=card_id,
                state=ScheduleState.REVIEW,
                step=None,
                stability=stability,
                difficulty=5.0,
                due_at=NOW,
                last_reviewed_at=NOW,
            )
        )


@pytest.fixture
def fx() -> _Fixture:
    return _Fixture()


async def test_plan_classifies_new_scheduled_and_skips_suspended(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Bio")
    reviewed_a = await fx.add_card(deck.id)
    reviewed_b = await fx.add_card(deck.id)
    _still_new = await fx.add_card(deck.id)
    suspended = await fx.add_card(deck.id)
    await fx.schedule(reviewed_a.id, 10.0)
    await fx.schedule(reviewed_b.id, 20.0)
    await fx.notes.suspend_card(owner_id=ALICE, card_id=suspended.id)

    goal = await fx.goals.create(
        owner_id=ALICE, name="Final", deck_ids=[deck.id], exam_date=EXAM, desired_grade=0.5
    )
    plan = await fx.planner.plan_for(owner_id=ALICE, goal_id=goal.id)

    assert plan.scheduled_cards == 2
    assert plan.new_cards == 1  # the suspended card is excluded
    assert plan.total_cards == 3
    assert plan.days_until_exam == 30
    assert plan.reviews_per_day >= 0
    # Everything fits in the time, so the forecast reaches the goal's target.
    assert plan.readiness == pytest.approx(goal.target_retention)
    assert plan.on_track is True


async def test_plan_for_a_foreign_goal_is_not_found(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Bio")
    goal = await fx.goals.create(
        owner_id=ALICE, name="Final", deck_ids=[deck.id], exam_date=EXAM, desired_grade=0.5
    )
    with pytest.raises(GoalNotFound):
        await fx.planner.plan_for(owner_id=BOB, goal_id=goal.id)


async def test_plan_over_an_empty_deck_is_well_formed(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Empty")
    goal = await fx.goals.create(
        owner_id=ALICE, name="Final", deck_ids=[deck.id], exam_date=EXAM, desired_grade=0.9
    )
    plan = await fx.planner.plan_for(owner_id=ALICE, goal_id=goal.id)
    assert plan.total_cards == 0
    assert plan.readiness == 0.0
