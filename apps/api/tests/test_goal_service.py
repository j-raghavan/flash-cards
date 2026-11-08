# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import InMemoryDeckRepository, InMemoryGoalRepository
from flashcards.application.deck_service import DeckService
from flashcards.application.goal_service import GoalService
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.goals import GoalNotFound

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
        self.service = GoalService(self.decks, InMemoryGoalRepository(), clock)


@pytest.fixture
def fx() -> _Fixture:
    return _Fixture()


async def test_create_over_owned_decks(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Bio")
    goal = await fx.service.create(
        owner_id=ALICE, name="Final", deck_ids=[deck.id], exam_date=EXAM, desired_grade=0.5
    )
    assert goal.deck_ids == (deck.id,)
    assert 0.80 <= goal.target_retention <= 0.97


async def test_create_rejects_a_foreign_deck(fx: _Fixture) -> None:
    bob_deck = await fx.decks.create(owner_id=BOB, name="Bob")
    with pytest.raises(DeckNotFound):
        await fx.service.create(
            owner_id=ALICE, name="Final", deck_ids=[bob_deck.id], exam_date=EXAM, desired_grade=0.5
        )


async def test_get_and_list_are_owner_scoped(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Bio")
    goal = await fx.service.create(
        owner_id=ALICE, name="Final", deck_ids=[deck.id], exam_date=EXAM, desired_grade=0.5
    )
    assert (await fx.service.get(owner_id=ALICE, goal_id=goal.id)).id == goal.id
    assert [g.id for g in await fx.service.list_goals(owner_id=ALICE)] == [goal.id]
    assert await fx.service.list_goals(owner_id=BOB) == []
    with pytest.raises(GoalNotFound):
        await fx.service.get(owner_id=BOB, goal_id=goal.id)


async def test_update_applies_each_change(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Bio")
    other = await fx.decks.create(owner_id=ALICE, name="Chem")
    goal = await fx.service.create(
        owner_id=ALICE, name="Final", deck_ids=[deck.id], exam_date=EXAM, desired_grade=0.0
    )
    later = EXAM + timedelta(days=10)
    updated = await fx.service.update(
        owner_id=ALICE,
        goal_id=goal.id,
        name="Retake",
        deck_ids=[deck.id, other.id],
        exam_date=later,
        desired_grade=1.0,
    )
    assert updated.name == "Retake"
    assert updated.deck_ids == (deck.id, other.id)
    assert updated.exam_date == later
    assert updated.target_retention == 0.97


async def test_update_with_no_fields_leaves_the_goal_unchanged(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Bio")
    goal = await fx.service.create(
        owner_id=ALICE, name="Final", deck_ids=[deck.id], exam_date=EXAM, desired_grade=0.5
    )
    same = await fx.service.update(owner_id=ALICE, goal_id=goal.id)
    assert same == goal


async def test_update_rejects_a_foreign_deck(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Bio")
    bob_deck = await fx.decks.create(owner_id=BOB, name="Bob")
    goal = await fx.service.create(
        owner_id=ALICE, name="Final", deck_ids=[deck.id], exam_date=EXAM, desired_grade=0.5
    )
    with pytest.raises(DeckNotFound):
        await fx.service.update(owner_id=ALICE, goal_id=goal.id, deck_ids=[bob_deck.id])


async def test_delete_is_owner_scoped(fx: _Fixture) -> None:
    deck = await fx.decks.create(owner_id=ALICE, name="Bio")
    goal = await fx.service.create(
        owner_id=ALICE, name="Final", deck_ids=[deck.id], exam_date=EXAM, desired_grade=0.5
    )
    with pytest.raises(GoalNotFound):
        await fx.service.delete(owner_id=BOB, goal_id=goal.id)
    await fx.service.delete(owner_id=ALICE, goal_id=goal.id)
    with pytest.raises(GoalNotFound):
        await fx.service.get(owner_id=ALICE, goal_id=goal.id)
