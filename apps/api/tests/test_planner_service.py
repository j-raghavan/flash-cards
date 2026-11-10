# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.db import plan_baseline_from_row, plan_baseline_to_row
from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryGoalRepository,
    InMemoryNoteRepository,
    InMemoryPlanBaselineRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
    InMemoryStudySettingsRepository,
    InMemoryStudyStatsRepository,
)
from flashcards.application.deck_service import DeckService
from flashcards.application.goal_service import GoalService
from flashcards.application.note_service import NoteService
from flashcards.application.planner_service import PlannerService
from flashcards.application.progress_service import ProgressService
from flashcards.domain.goals import GoalNotFound
from flashcards.domain.notes import Card, NoteType
from flashcards.domain.planner import ChangeReason, PlanBaseline, StudyPlan
from flashcards.domain.scheduling import CardSchedule, ScheduleState

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
EXAM = NOW + timedelta(days=30)
ALICE = "alice"
BOB = "bob"


class FakeClock:
    def __init__(self) -> None:
        self.current = NOW

    def now(self) -> datetime:
        return self.current


class _Fixture:
    def __init__(self) -> None:
        self.clock = clock = FakeClock()
        self.decks = DeckService(InMemoryDeckRepository(), clock)
        self.cards = InMemoryCardRepository()
        self.schedules = InMemoryScheduleRepository()
        self.logs = InMemoryReviewLogRepository()
        self.notes = NoteService(self.decks, InMemoryNoteRepository(), self.cards, clock)
        goal_repo = InMemoryGoalRepository()
        self.baselines = InMemoryPlanBaselineRepository(goal_repo)
        self.goals = GoalService(self.decks, goal_repo, clock)
        self.progress = ProgressService(
            self.decks,
            InMemoryStudySettingsRepository(),
            InMemoryStudyStatsRepository(self.cards, self.schedules, self.logs),
            clock,
        )
        self.planner = PlannerService(
            self.goals,
            self.cards,
            self.schedules,
            self.baselines,
            self.progress,
            clock,
        )

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
            ALICE,
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


async def _goal(fx: _Fixture, deck_id: str, grade: float = 0.5) -> str:
    goal = await fx.goals.create(
        owner_id=ALICE, name="Final", deck_ids=[deck_id], exam_date=EXAM, desired_grade=grade
    )
    return goal.id


async def test_first_report_sets_the_baseline_without_changes(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Bio")
    await fx.add_card(deck.id)
    goal_id = await _goal(fx, deck.id)

    first = await fx.planner.report(owner_id=ALICE, goal_id=goal_id)
    assert first.change is None
    again = await fx.planner.report(owner_id=ALICE, goal_id=goal_id)
    assert again.change is not None
    assert again.change.changed is False and again.change.reasons == ()
    assert again.change.since == NOW


async def test_report_explains_added_cards_and_missed_days(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Bio")
    for _ in range(20):
        await fx.add_card(deck.id)
    goal_id = await _goal(fx, deck.id)
    await fx.planner.report(owner_id=ALICE, goal_id=goal_id)  # baseline: 20 new, 30 days

    fx.clock.current = NOW + timedelta(days=4)  # four days later, nothing studied
    for _ in range(10):
        await fx.add_card(deck.id)
    report = await fx.planner.report(owner_id=ALICE, goal_id=goal_id)

    change = report.change
    assert change is not None
    assert dict(change.reasons) == {
        ChangeReason.CARDS_ADDED: 10,
        ChangeReason.MISSED_DAYS: 3,
        ChangeReason.DAYS_PASSED: 4,
    }
    assert change.before.new_per_day == 1  # ceil(20 / 30)
    assert report.plan.new_per_day == 2  # ceil(30 / 26)
    assert (change.daily_load_before, change.daily_load_after) == (1, 2)
    assert change.changed is True


async def test_report_explains_progress_removals_and_goal_edits(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Bio")
    cards = [await fx.add_card(deck.id) for _ in range(4)]
    goal_id = await _goal(fx, deck.id)
    await fx.planner.report(owner_id=ALICE, goal_id=goal_id)

    await fx.schedule(cards[0].id, 10.0)  # started one new card
    await fx.notes.suspend_card(owner_id=ALICE, card_id=cards[1].id)  # dropped one
    await fx.goals.update(
        owner_id=ALICE, goal_id=goal_id, exam_date=EXAM + timedelta(days=10), desired_grade=1.0
    )
    change = (await fx.planner.report(owner_id=ALICE, goal_id=goal_id)).change

    assert change is not None
    assert dict(change.reasons) == {
        ChangeReason.EXAM_DATE_CHANGED: 0,
        ChangeReason.TARGET_CHANGED: 0,
        ChangeReason.CARDS_REMOVED: 1,
        ChangeReason.CARDS_STARTED: 1,
    }


async def test_accepting_the_plan_resets_the_baseline(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Bio")
    goal_id = await _goal(fx, deck.id)
    await fx.planner.report(owner_id=ALICE, goal_id=goal_id)
    await fx.add_card(deck.id)
    fx.clock.current = NOW + timedelta(hours=2)

    accepted = await fx.planner.accept(owner_id=ALICE, goal_id=goal_id)
    assert accepted.total_cards == 1
    change = (await fx.planner.report(owner_id=ALICE, goal_id=goal_id)).change
    assert change is not None
    assert change.reasons == () and change.since == NOW + timedelta(hours=2)
    with pytest.raises(GoalNotFound):
        await fx.planner.accept(owner_id=BOB, goal_id=goal_id)


def test_baseline_round_trip_through_rows() -> None:
    baseline = PlanBaseline(
        goal_id="g1",
        owner_id=ALICE,
        taken_at=NOW,
        exam_date=EXAM,
        target_retention=0.9,
        plan=StudyPlan(
            days_until_exam=30,
            total_cards=10,
            new_cards=4,
            scheduled_cards=6,
            new_per_day=1,
            reviews_per_day=2,
            readiness=0.88,
            on_track=False,
        ),
    )
    assert plan_baseline_from_row(plan_baseline_to_row(baseline)) == baseline


async def test_a_deleted_goal_takes_its_baseline_with_it(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Bio")
    goal_id = await _goal(fx, deck.id)
    await fx.planner.report(owner_id=ALICE, goal_id=goal_id)
    assert await fx.baselines.get(goal_id) is not None
    await fx.goals.delete(owner_id=ALICE, goal_id=goal_id)
    assert await fx.baselines.get(goal_id) is None
