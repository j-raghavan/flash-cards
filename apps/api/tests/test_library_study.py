# SPDX-License-Identifier: AGPL-3.0-only
"""Studying library decks with per-member progress (ADR 0024)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.fsrs_scheduler import FsrsScheduler
from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryMediaRepository,
    InMemoryNoteRepository,
    InMemoryNoteTypeRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
    InMemoryStudySettingsRepository,
    InMemoryStudyStatsRepository,
    InMemoryUserRepository,
)
from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.application.progress_service import ProgressService
from flashcards.application.read_access import ReadAccess
from flashcards.application.review_service import ReviewService
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.invites import Role
from flashcards.domain.notes import CardNotFound, NoteType
from flashcards.domain.scheduling import Rating
from flashcards.domain.users import User

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
ADMIN, MEMBER = "admin", "member"


class FakeClock:
    def __init__(self) -> None:
        self.current = NOW

    def now(self) -> datetime:
        return self.current


class _Rig:
    def __init__(self) -> None:
        self.clock = FakeClock()
        self.users = InMemoryUserRepository()
        deck_repo, note_repo = InMemoryDeckRepository(), InMemoryNoteRepository()
        self.cards = InMemoryCardRepository()
        self.schedules = InMemoryScheduleRepository()
        self.logs = InMemoryReviewLogRepository()
        self.decks = DeckService(deck_repo, self.clock)
        self.notes = NoteService(self.decks, note_repo, self.cards, self.clock)
        access = ReadAccess(
            self.users,
            deck_repo,
            note_repo,
            self.cards,
            InMemoryNoteTypeRepository(),
            InMemoryMediaRepository(),
        )
        self.reviews = ReviewService(access, self.schedules, self.logs, FsrsScheduler(), self.clock)
        self.progress = ProgressService(
            access,
            InMemoryStudySettingsRepository(),
            InMemoryStudyStatsRepository(self.cards, self.schedules, self.logs),
            self.clock,
        )

    async def seed(self) -> None:
        for user_id, role in ((ADMIN, Role.ADMIN), (MEMBER, Role.MEMBER)):
            await self.users.add(
                User(
                    id=user_id,
                    email=f"{user_id}@example.com",
                    password_hash="h",
                    role=role,
                    created_at=NOW,
                )
            )
        deck = await self.decks.create(owner_id=ADMIN, name="Library")
        self.deck_id = deck.id
        self.card_ids: list[str] = []
        for front in ("Q1", "Q2"):
            _note, cards = await self.notes.create_note(
                owner_id=ADMIN,
                deck_id=deck.id,
                note_type=NoteType.BASIC,
                fields={"front": front, "back": "A"},
            )
            self.card_ids.append(cards[0].id)

    async def answer(self, user_id: str, card_id: str, rating: Rating = Rating.GOOD) -> str:
        outcome = await self.reviews.submit(owner_id=user_id, card_id=card_id, rating=rating)
        return outcome.review_id

    async def queue(self, user_id: str) -> list[str]:
        return [c.id for c in await self.reviews.due_queue(owner_id=user_id, deck_id=self.deck_id)]

    async def leave_library(self, how: str) -> None:
        admin = await self.users.get(ADMIN)
        assert admin is not None
        changed = admin.with_role(Role.MEMBER) if how == "demote" else admin.with_suspended(NOW)
        await self.users.update(changed)


@pytest.fixture
async def rig() -> _Rig:
    built = _Rig()
    await built.seed()
    return built


async def test_member_progress_on_a_library_card_is_their_own(rig: _Rig) -> None:
    first, second = rig.card_ids

    await rig.answer(MEMBER, first)

    assert await rig.schedules.get(MEMBER, first) is not None
    assert await rig.schedules.get(ADMIN, first) is None
    assert await rig.queue(MEMBER) == [second]
    assert await rig.queue(ADMIN) == [first, second]  # the admin's queue is unchanged
    member_stats = await rig.reviews.deck_stats(owner_id=MEMBER, deck_id=rig.deck_id)
    admin_stats = await rig.reviews.deck_stats(owner_id=ADMIN, deck_id=rig.deck_id)
    assert (member_stats.new, member_stats.due) == (1, 1)
    assert (admin_stats.new, admin_stats.due, admin_stats.total) == (2, 2, 2)
    assert [log.owner_id for log in await rig.logs.list_for_owner(MEMBER)] == [MEMBER]
    assert await rig.logs.list_for_owner(ADMIN) == []


async def test_member_undo_after_the_admin_answered_the_same_card(rig: _Rig) -> None:
    card = rig.card_ids[0]
    await rig.answer(ADMIN, card)
    admin_row = await rig.schedules.get(ADMIN, card)
    await rig.answer(MEMBER, card, Rating.AGAIN)
    member_first = await rig.schedules.get(MEMBER, card)
    rig.clock.current += timedelta(minutes=1)
    review_id = await rig.answer(MEMBER, card)

    undone = await rig.reviews.undo(owner_id=MEMBER, review_id=review_id)

    assert undone.schedule == member_first
    assert await rig.schedules.get(MEMBER, card) == member_first
    assert await rig.schedules.get(ADMIN, card) == admin_row


async def test_undo_of_a_members_first_answer_keeps_the_admins_row(rig: _Rig) -> None:
    card = rig.card_ids[0]
    await rig.answer(ADMIN, card)
    admin_row = await rig.schedules.get(ADMIN, card)
    review_id = await rig.answer(MEMBER, card)

    await rig.reviews.undo(owner_id=MEMBER, review_id=review_id)

    assert await rig.schedules.get(MEMBER, card) is None
    assert await rig.schedules.get(ADMIN, card) == admin_row
    assert await rig.queue(MEMBER) == rig.card_ids  # new again for the member


async def test_the_admins_bury_hides_a_card_only_from_the_admin(rig: _Rig) -> None:
    first, second = rig.card_ids
    card = await rig.cards.get(first)
    assert card is not None
    await rig.cards.update(card.buried(NOW).with_flag(2))

    assert await rig.queue(ADMIN) == [second]
    assert await rig.queue(MEMBER) == [first, second]
    member_queue = await rig.reviews.due_queue(owner_id=MEMBER, deck_id=rig.deck_id)
    assert [(c.flag, c.buried_until) for c in member_queue] == [(0, None), (0, None)]
    admin_stats = await rig.reviews.deck_stats(owner_id=ADMIN, deck_id=rig.deck_id)
    member_stats = await rig.reviews.deck_stats(owner_id=MEMBER, deck_id=rig.deck_id)
    assert (admin_stats.due, member_stats.due) == (1, 2)


async def test_the_admins_suspension_hides_a_card_from_everyone(rig: _Rig) -> None:
    first, second = rig.card_ids
    await rig.notes.suspend_card(owner_id=ADMIN, card_id=first)

    assert await rig.queue(MEMBER) == [second]
    stats = await rig.reviews.deck_stats(owner_id=MEMBER, deck_id=rig.deck_id)
    assert (stats.suspended, stats.due) == (1, 1)


async def test_member_stats_and_forecast_cover_library_cards(rig: _Rig) -> None:
    await rig.answer(MEMBER, rig.card_ids[0])

    overall = await rig.progress.stats(owner_id=MEMBER, days=1, forecast_days=7)
    per_deck = await rig.progress.stats(
        owner_id=MEMBER, deck_id=rig.deck_id, days=1, forecast_days=7
    )
    admin = await rig.progress.stats(owner_id=ADMIN, deck_id=rig.deck_id, days=1)

    assert sum(d.due for d in overall.forecast) == 1
    assert [d.reviews for d in per_deck.heatmap] == [1]
    assert [d.reviews for d in admin.heatmap] == [0]
    assert sum(d.due for d in admin.forecast) == 0


@pytest.mark.parametrize("how", ["demote", "suspend"])
async def test_a_library_deck_closes_when_its_admin_leaves(rig: _Rig, how: str) -> None:
    card = rig.card_ids[0]
    await rig.answer(MEMBER, card)

    await rig.leave_library(how)

    with pytest.raises(CardNotFound):
        await rig.answer(MEMBER, rig.card_ids[1])
    with pytest.raises(DeckNotFound):
        await rig.reviews.due_queue(owner_id=MEMBER, deck_id=rig.deck_id)
    with pytest.raises(DeckNotFound):
        await rig.reviews.deck_stats(owner_id=MEMBER, deck_id=rig.deck_id)
    with pytest.raises(DeckNotFound):
        await rig.progress.stats(owner_id=MEMBER, deck_id=rig.deck_id)
    overall = await rig.progress.stats(owner_id=MEMBER, forecast_days=7)
    assert sum(d.due for d in overall.forecast) == 0  # the closed deck drops out
    assert await rig.schedules.get(MEMBER, card) is not None  # kept, unused
    # The former admin still studies their own deck as before.
    assert await rig.queue(ADMIN) == rig.card_ids
