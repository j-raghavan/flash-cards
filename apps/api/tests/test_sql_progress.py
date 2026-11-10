# SPDX-License-Identifier: AGPL-3.0-only
"""SQL adapters for study settings and study statistics, checked against a real
Postgres and against the in-memory adapters (needs Docker)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flashcards.adapters.db import Base, create_engine, create_sessionmaker
from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
    InMemoryStudyStatsRepository,
)
from flashcards.adapters.sql import (
    SqlCardRepository,
    SqlDeckRepository,
    SqlNoteRepository,
    SqlReviewLogRepository,
    SqlScheduleRepository,
    SqlStudySettingsRepository,
    SqlStudyStatsRepository,
)
from flashcards.domain.decks import Deck
from flashcards.domain.notes import Card, Note, NoteType
from flashcards.domain.progress import ActivityBucket, DueBucket, StudySettings
from flashcards.domain.scheduling import (
    CardSchedule,
    Rating,
    ReviewLog,
    ScheduleState,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


def _card(card_id: str, deck_id: str, note_id: str, owner: str = "alice") -> Card:
    return Card(
        id=card_id, owner_id=owner, deck_id=deck_id, note_id=note_id, ordinal=0, created_at=NOW
    )


def _note(note_id: str, deck_id: str, owner: str = "alice") -> Note:
    return Note(
        id=note_id,
        owner_id=owner,
        deck_id=deck_id,
        note_type=NoteType.BASIC,
        fields=(("front", note_id), ("back", "A")),
        created_at=NOW,
    )


def _schedule(card_id: str, due_at: datetime) -> CardSchedule:
    return CardSchedule(
        card_id=card_id,
        state=ScheduleState.REVIEW,
        step=None,
        stability=4.0,
        difficulty=5.0,
        due_at=due_at,
        last_reviewed_at=due_at - timedelta(days=4),
    )


def _log(
    log_id: str, card_id: str, at: datetime, rating: Rating, owner: str = "alice"
) -> ReviewLog:
    return ReviewLog(id=log_id, owner_id=owner, card_id=card_id, rating=rating, reviewed_at=at)


# Cards: a1, a2 in deck A; b1 (suspended) in deck B; z1 is bob's.
CARDS = [
    _card("a1", "dA", "na1"),
    _card("a2", "dA", "na2"),
    _card("b1", "dB", "nb1").suspended(NOW),
    _card("z1", "dZ", "nz1", owner="bob"),
]
OWNER_OF = {card.id: card.owner_id for card in CARDS}  # each card's schedule is its owner's
# Bob's own progress on Alice's card (ADR 0024): never part of Alice's forecast.
OTHER_USERS_SCHEDULES = [("bob", _schedule("a1", NOW + timedelta(days=3)))]
LOGS = [
    _log("l1", "a1", NOW - timedelta(days=2, minutes=3), Rating.AGAIN),
    _log("l2", "a1", NOW - timedelta(days=2, minutes=1), Rating.GOOD),
    _log("l3", "a2", NOW - timedelta(hours=5, seconds=7), Rating.HARD),
    _log("l4", "b1", NOW - timedelta(hours=1), Rating.EASY),
    _log("l5", "z1", NOW - timedelta(hours=1), Rating.GOOD, owner="bob"),
    _log("l6", "a2", NOW - timedelta(days=40), Rating.GOOD),  # outside the range
]
SCHEDULES = [
    _schedule("a1", NOW - timedelta(days=9)),  # overdue
    _schedule("a2", NOW + timedelta(days=2, minutes=20)),
    _schedule("b1", NOW + timedelta(days=1)),  # suspended
    _schedule("z1", NOW + timedelta(days=1)),  # bob's
]


async def _seed(sm: async_sessionmaker[AsyncSession]) -> None:
    async with sm() as s, s.begin():
        for deck_id, owner in (("dA", "alice"), ("dB", "alice"), ("dZ", "bob")):
            await SqlDeckRepository(s).add(
                Deck(id=deck_id, owner_id=owner, name=deck_id, parent_id=None, created_at=NOW)
            )
        for card in CARDS:
            await SqlNoteRepository(s).add(_note(card.note_id, card.deck_id, card.owner_id))
            await SqlCardRepository(s).add(card)
        for schedule in SCHEDULES:
            await SqlScheduleRepository(s).upsert(OWNER_OF[schedule.card_id], schedule)
        for user, schedule in OTHER_USERS_SCHEDULES:
            await SqlScheduleRepository(s).upsert(user, schedule)
        for log in LOGS:
            await SqlReviewLogRepository(s).append(log)


async def _memory_stats() -> InMemoryStudyStatsRepository:
    cards, schedules, logs = (
        InMemoryCardRepository(),
        InMemoryScheduleRepository(),
        InMemoryReviewLogRepository(),
    )
    for card in CARDS:
        await cards.add(card)
    for schedule in SCHEDULES:
        await schedules.upsert(OWNER_OF[schedule.card_id], schedule)
    for user, schedule in OTHER_USERS_SCHEDULES:
        await schedules.upsert(user, schedule)
    for log in LOGS:
        await logs.append(log)
    return InMemoryStudyStatsRepository(cards, schedules, logs)


async def test_activity_and_forecast_match_the_memory_adapter(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    await _seed(sm)
    memory = await _memory_stats()
    since, until = NOW - timedelta(days=7), NOW + timedelta(minutes=1)
    floor, horizon = NOW - timedelta(hours=12), NOW + timedelta(days=5)

    async with sm() as s:
        sql = SqlStudyStatsRepository(s)
        for deck_ids in (None, ["dA"], ["dB"], []):
            sql_activity = await sql.review_activity(
                "alice", since=since, until=until, deck_ids=deck_ids
            )
            assert sql_activity == await memory.review_activity(
                "alice", since=since, until=until, deck_ids=deck_ids
            )
            sql_due = await sql.due_forecast("alice", floor=floor, until=horizon, deck_ids=deck_ids)
            assert sql_due == await memory.due_forecast(
                "alice", floor=floor, until=horizon, deck_ids=deck_ids
            )
        overall = await sql.review_activity("alice", since=since, until=until)
        due = await sql.due_forecast("alice", floor=floor, until=horizon)

    day_two = (NOW - timedelta(days=2, minutes=3)).replace(minute=45, second=0)
    assert overall == [
        ActivityBucket(day_two, 2, 1),  # Again + Good in one quarter hour
        ActivityBucket(datetime(2026, 11, 8, 6, 45, tzinfo=UTC), 1, 1),
        ActivityBucket(datetime(2026, 11, 8, 11, 0, tzinfo=UTC), 1, 1),
    ]
    # The overdue card is clamped to the floor; the suspended and foreign ones are absent,
    # and so is Bob's own schedule on Alice's card.
    assert due == [
        DueBucket(floor, 1),
        DueBucket(datetime(2026, 11, 10, 12, 15, tzinfo=UTC), 1),
    ]


async def test_settings_upsert_and_delete(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        repo = SqlStudySettingsRepository(s)
        assert await repo.get("alice") is None
        await repo.save(StudySettings("alice", 10, "UTC"))
        assert await repo.get("alice") == StudySettings("alice", 10, "UTC")
        await repo.save(StudySettings("alice", 25, "Europe/Berlin"))  # same session
        assert await repo.get("alice") == StudySettings("alice", 25, "Europe/Berlin")
        await repo.save(StudySettings("bob", 5, "UTC"))
    async with sm() as s, s.begin():
        repo = SqlStudySettingsRepository(s)
        await repo.delete_for_owner("alice")
    async with sm() as s:
        repo = SqlStudySettingsRepository(s)
        assert await repo.get("alice") is None
        assert await repo.get("bob") == StudySettings("bob", 5, "UTC")
