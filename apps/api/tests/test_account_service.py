# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryGoalRepository,
    InMemoryNoteRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
)
from flashcards.application.account_service import AccountService
from flashcards.application.deck_service import DeckService
from flashcards.application.goal_service import GoalService
from flashcards.application.note_service import NoteService
from flashcards.domain.invites import Role
from flashcards.domain.notes import NoteType
from flashcards.domain.scheduling import CardSchedule, Rating, ReviewLog, ScheduleState
from flashcards.domain.users import User

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ALICE = User(
    id="alice",
    email="alice@example.com",
    password_hash="hash",
    role=Role.MEMBER,
    created_at=NOW,
)
BOB = "bob"


class FakeClock:
    def now(self) -> datetime:
        return NOW


async def test_export_gathers_everything_the_user_owns() -> None:
    clock = FakeClock()
    deck_repo = InMemoryDeckRepository()
    decks = DeckService(deck_repo, clock)
    note_repo, card_repo = InMemoryNoteRepository(), InMemoryCardRepository()
    schedule_repo, log_repo = InMemoryScheduleRepository(), InMemoryReviewLogRepository()
    goal_repo = InMemoryGoalRepository()
    notes = NoteService(decks, note_repo, card_repo, clock)
    goals = GoalService(decks, goal_repo, clock)
    account = AccountService(
        deck_repo, note_repo, card_repo, schedule_repo, log_repo, goal_repo
    )

    deck = await decks.create(owner_id=ALICE.id, name="Biology")
    _n1, cards1 = await notes.create_note(
        owner_id=ALICE.id, deck_id=deck.id, note_type=NoteType.BASIC,
        fields={"front": "q1", "back": "a1"},
    )
    await notes.create_note(
        owner_id=ALICE.id, deck_id=deck.id, note_type=NoteType.BASIC,
        fields={"front": "q2", "back": "a2"},
    )
    reviewed = cards1[0]
    await schedule_repo.upsert(
        CardSchedule(
            card_id=reviewed.id,
            state=ScheduleState.REVIEW,
            step=None,
            stability=10.0,
            difficulty=5.0,
            due_at=NOW,
            last_reviewed_at=NOW,
        )
    )
    await log_repo.append(
        ReviewLog(
            id=str(uuid.uuid4()),
            owner_id=ALICE.id,
            card_id=reviewed.id,
            rating=Rating.GOOD,
            reviewed_at=NOW,
        )
    )
    await goals.create(
        owner_id=ALICE.id, name="Final", deck_ids=[deck.id],
        exam_date=NOW + timedelta(days=30), desired_grade=0.5,
    )
    # Noise owned by someone else must not leak into the export.
    await decks.create(owner_id=BOB, name="Bob's deck")

    export = await account.export(user=ALICE)

    assert export.user is ALICE
    assert [d.name for d in export.decks] == ["Biology"]
    assert len(export.notes) == 2
    assert len(export.cards) == 2
    assert [s.card_id for s in export.schedules] == [reviewed.id]
    assert len(export.review_logs) == 1
    assert [g.name for g in export.goals] == ["Final"]
