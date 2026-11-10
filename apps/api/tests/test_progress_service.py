# SPDX-License-Identifier: AGPL-3.0-only
"""Study settings, today's goal and streak, and statistics (F-PROG-1, F-PROG-3)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from flashcards.adapters.db import study_settings_from_row, study_settings_to_row
from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryNoteRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
    InMemoryStudySettingsRepository,
    InMemoryStudyStatsRepository,
)
from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.application.progress_service import ProgressService
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.notes import NoteType
from flashcards.domain.progress import InvalidRange, InvalidStudySettings, StudySettings
from flashcards.domain.scheduling import CardSchedule, Rating, ReviewLog, ScheduleState

# 10:00 UTC on Sunday 8 November 2026 (11:00 in Berlin, 15:30 in Kolkata).
NOW = datetime(2026, 11, 8, 10, 0, tzinfo=UTC)
ALICE = "alice"
BOB = "bob"


class FakeClock:
    def __init__(self) -> None:
        self.current = NOW

    def now(self) -> datetime:
        return self.current


class _Fixture:
    def __init__(self) -> None:
        self.clock = FakeClock()
        self.decks = DeckService(InMemoryDeckRepository(), self.clock)
        self.cards = InMemoryCardRepository()
        self.schedules = InMemoryScheduleRepository()
        self.logs = InMemoryReviewLogRepository()
        self.notes = NoteService(self.decks, InMemoryNoteRepository(), self.cards, self.clock)
        self.settings = InMemoryStudySettingsRepository()
        self.stats = InMemoryStudyStatsRepository(self.cards, self.schedules, self.logs)
        self.progress = ProgressService(self.decks, self.settings, self.stats, self.clock)

    async def deck_with_cards(self, owner: str, count: int, name: str = "Deck") -> list[str]:
        deck = await self.decks.create(owner_id=owner, name=name)
        ids = []
        for n in range(count):
            _note, cards = await self.notes.create_note(
                owner_id=owner,
                deck_id=deck.id,
                note_type=NoteType.BASIC,
                fields={"front": f"q{n}", "back": "a"},
            )
            ids.append(cards[0].id)
        return ids

    async def deck_of(self, card_id: str) -> str:
        card = await self.cards.get(card_id)
        assert card is not None
        return card.deck_id

    async def review(
        self, owner: str, card_id: str, at: datetime, rating: Rating = Rating.GOOD
    ) -> None:
        await self.logs.append(
            ReviewLog(
                id=str(uuid.uuid4()), owner_id=owner, card_id=card_id, rating=rating, reviewed_at=at
            )
        )

    async def due(self, card_id: str, at: datetime) -> None:
        await self.schedules.upsert(
            ALICE,
            CardSchedule(
                card_id=card_id,
                state=ScheduleState.REVIEW,
                step=None,
                stability=5.0,
                difficulty=5.0,
                due_at=at,
                last_reviewed_at=at - timedelta(days=5),
            )
        )


@pytest.fixture
def fx() -> _Fixture:
    return _Fixture()


async def test_settings_default_then_partial_updates(fx: _Fixture) -> None:
    assert await fx.progress.get_settings(owner_id=ALICE) == StudySettings(ALICE)
    updated = await fx.progress.update_settings(owner_id=ALICE, daily_goal=5)
    assert updated == StudySettings(ALICE, 5, "UTC")
    updated = await fx.progress.update_settings(owner_id=ALICE, time_zone="Asia/Kolkata")
    assert updated == StudySettings(ALICE, 5, "Asia/Kolkata")
    assert await fx.progress.get_settings(owner_id=ALICE) == updated
    assert await fx.progress.get_settings(owner_id=BOB) == StudySettings(BOB)


async def test_invalid_settings_change_nothing(fx: _Fixture) -> None:
    with pytest.raises(InvalidStudySettings):
        await fx.progress.update_settings(owner_id=ALICE, time_zone="Nowhere/Land")
    with pytest.raises(InvalidStudySettings):
        await fx.progress.update_settings(owner_id=ALICE, daily_goal=0)
    assert await fx.settings.get(ALICE) is None


async def test_today_counts_against_the_goal_in_the_users_zone(fx: _Fixture) -> None:
    [card] = await fx.deck_with_cards(ALICE, 1)
    await fx.progress.update_settings(owner_id=ALICE, daily_goal=2)
    # 23:30 UTC yesterday is already today in Berlin, but not in UTC.
    await fx.review(ALICE, card, datetime(2026, 11, 7, 23, 30, tzinfo=UTC))
    await fx.review(ALICE, card, NOW - timedelta(hours=1))
    await fx.review(BOB, card, NOW)  # someone else's review never counts

    utc = await fx.progress.today(owner_id=ALICE)
    assert (utc.day, utc.reviews, utc.goal_met, utc.streak) == (date(2026, 11, 8), 1, False, 0)

    await fx.progress.update_settings(owner_id=ALICE, time_zone="Europe/Berlin")
    berlin = await fx.progress.today(owner_id=ALICE, with_streak=False)
    assert (berlin.reviews, berlin.just_completed, berlin.streak) == (2, True, None)
    assert berlin.time_zone == "Europe/Berlin"


async def test_streak_from_review_history(fx: _Fixture) -> None:
    [card] = await fx.deck_with_cards(ALICE, 1)
    await fx.progress.update_settings(owner_id=ALICE, daily_goal=1)
    for days_ago in (1, 2, 3, 5):
        await fx.review(ALICE, card, NOW - timedelta(days=days_ago))
    assert (await fx.progress.today(owner_id=ALICE)).streak == 3  # today not yet done
    await fx.review(ALICE, card, NOW)
    assert (await fx.progress.today(owner_id=ALICE)).streak == 4


async def test_stats_heatmap_retention_and_forecast(fx: _Fixture) -> None:
    a, b, suspended, later = await fx.deck_with_cards(ALICE, 4)
    await fx.review(ALICE, a, NOW - timedelta(days=1), Rating.AGAIN)
    await fx.review(ALICE, a, NOW - timedelta(days=1), Rating.GOOD)
    await fx.review(ALICE, b, NOW, Rating.EASY)
    await fx.review(ALICE, b, NOW - timedelta(days=10))  # outside a 7-day window
    await fx.due(a, NOW - timedelta(days=3))  # overdue: counts today
    await fx.due(b, NOW + timedelta(days=2))
    await fx.due(suspended, NOW + timedelta(days=1))
    await fx.due(later, NOW + timedelta(days=5))  # past the 5-day forecast
    await fx.notes.suspend_card(owner_id=ALICE, card_id=suspended)

    stats = await fx.progress.stats(owner_id=ALICE, days=7, forecast_days=5)

    assert stats.time_zone == "UTC" and stats.deck_id is None
    assert [d.day for d in stats.heatmap][-1] == date(2026, 11, 8)
    assert [d.reviews for d in stats.heatmap] == [0, 0, 0, 0, 0, 2, 1]
    # Saturday 7th and Sunday 8th are the same Monday-start week.
    assert [(p.week_start, p.reviews, p.recalled) for p in stats.retention] == [
        (date(2026, 11, 2), 3, 2)
    ]
    assert [d.due for d in stats.forecast] == [1, 0, 1, 0, 0]


async def test_stats_per_deck_and_ownership(fx: _Fixture) -> None:
    [mine] = await fx.deck_with_cards(ALICE, 1, "Mine")
    [other] = await fx.deck_with_cards(ALICE, 1, "Other")
    await fx.review(ALICE, mine, NOW)
    await fx.review(ALICE, other, NOW)
    await fx.due(other, NOW)
    deck_id = await fx.deck_of(mine)

    per_deck = await fx.progress.stats(owner_id=ALICE, deck_id=deck_id, days=1, forecast_days=1)

    assert per_deck.deck_id == deck_id
    assert [d.reviews for d in per_deck.heatmap] == [1]
    assert [d.due for d in per_deck.forecast] == [0]
    with pytest.raises(DeckNotFound):
        await fx.progress.stats(owner_id=BOB, deck_id=deck_id)


async def test_stats_ranges_are_bounded(fx: _Fixture) -> None:
    for days, forecast_days in ((0, 1), (367, 1), (1, 0), (1, 91)):
        with pytest.raises(InvalidRange):
            await fx.progress.stats(owner_id=ALICE, days=days, forecast_days=forecast_days)


async def test_missed_days_since_counts_empty_days_in_the_decks(fx: _Fixture) -> None:
    [card] = await fx.deck_with_cards(ALICE, 1)
    [elsewhere] = await fx.deck_with_cards(ALICE, 1, "Elsewhere")
    deck_id = await fx.deck_of(card)
    await fx.review(ALICE, card, NOW - timedelta(days=3))
    await fx.review(ALICE, elsewhere, NOW - timedelta(days=2))  # another deck: still missed
    missed = await fx.progress.missed_days_since(
        owner_id=ALICE, deck_ids=[deck_id], since=NOW - timedelta(days=5)
    )
    # The 4th..7th of November; the 5th had a review in the deck.
    assert missed == 3


def test_settings_round_trip_through_rows() -> None:
    settings = StudySettings("alice", 12, "Asia/Kolkata")
    assert study_settings_from_row(study_settings_to_row(settings)) == settings
