# SPDX-License-Identifier: AGPL-3.0-only
"""Integration tests for the SQL goal adapter against a real Postgres (needs Docker)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from flashcards.adapters.db import Base, create_engine, create_sessionmaker
from flashcards.adapters.sql import SqlGoalRepository, SqlPlanBaselineRepository
from flashcards.domain.goals import Goal, new_goal
from flashcards.domain.planner import PlanBaseline, StudyPlan

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
EXAM = NOW + timedelta(days=30)


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


def _goal(owner: str, name: str, decks: list[str], created: datetime) -> Goal:
    goal = new_goal(
        owner_id=owner,
        name=name,
        deck_ids=decks,
        exam_date=EXAM,
        desired_grade=0.5,
        now=created,
    )
    return goal


async def test_goal_roundtrip_update_and_list_order(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    first = _goal("alice", "Bio", ["d1", "d2"], NOW)
    second = _goal("alice", "Chem", ["d3"], NOW + timedelta(hours=1))
    async with sm() as s, s.begin():
        repo = SqlGoalRepository(s)
        await repo.add(first)
        await repo.add(second)
        await repo.add(_goal("bob", "Physics", ["d9"], NOW))

    async with sm() as s:
        repo = SqlGoalRepository(s)
        loaded = await repo.get(first.id)
        assert loaded is not None
        assert loaded.deck_ids == ("d1", "d2")  # JSON list round-trips to a tuple
        assert loaded.target_retention == first.target_retention
        # Newest first, and scoped to the owner.
        assert [g.name for g in await repo.list_for_owner("alice")] == ["Chem", "Bio"]
        assert await repo.get("missing") is None

    async with sm() as s, s.begin():
        repo = SqlGoalRepository(s)
        await repo.update(first.renamed("Biology").with_decks(["d1"]))

    async with sm() as s, s.begin():
        repo = SqlGoalRepository(s)
        reloaded = await repo.get(first.id)
        assert reloaded is not None
        assert reloaded.name == "Biology" and reloaded.deck_ids == ("d1",)
        await repo.delete(first.id)
        await repo.delete("missing")  # no-op

    async with sm() as s:
        assert await SqlGoalRepository(s).get(first.id) is None


def _baseline(goal_id: str, taken_at: datetime, new_cards: int) -> PlanBaseline:
    return PlanBaseline(
        goal_id=goal_id,
        owner_id="alice",
        taken_at=taken_at,
        exam_date=NOW + timedelta(days=30),
        target_retention=0.885,
        plan=StudyPlan(
            days_until_exam=30,
            total_cards=new_cards,
            new_cards=new_cards,
            scheduled_cards=0,
            new_per_day=1,
            reviews_per_day=0,
            readiness=0.885,
            on_track=True,
        ),
    )


async def test_baseline_first_one_wins_save_replaces_and_goal_delete_cascades(
    engine: AsyncEngine,
) -> None:
    sm = create_sessionmaker(engine)
    goal = new_goal(
        owner_id="alice",
        name="Final",
        deck_ids=["dA"],
        exam_date=NOW + timedelta(days=30),
        desired_grade=0.5,
        now=NOW,
    )
    async with sm() as s, s.begin():
        await SqlGoalRepository(s).add(goal)
        repo = SqlPlanBaselineRepository(s)
        assert await repo.get(goal.id) is None
        first = await repo.add_if_absent(_baseline(goal.id, NOW, 3))
        second = await repo.add_if_absent(_baseline(goal.id, NOW + timedelta(hours=1), 9))
        assert first == second == _baseline(goal.id, NOW, 3)
        await repo.save(_baseline(goal.id, NOW + timedelta(days=1), 7))
        assert await repo.get(goal.id) == _baseline(goal.id, NOW + timedelta(days=1), 7)
    async with sm() as s, s.begin():
        await SqlGoalRepository(s).delete(goal.id)
    async with sm() as s:
        assert await SqlPlanBaselineRepository(s).get(goal.id) is None
