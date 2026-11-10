# SPDX-License-Identifier: AGPL-3.0-only
"""Undoing the last answer and editing a card mid-session (F-STUDY-3)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.adapters.db import review_log_from_row, review_log_to_row
from flashcards.adapters.fsrs_scheduler import FsrsScheduler
from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryMediaRepository,
    InMemoryMockExamRepository,
    InMemoryNoteRepository,
    InMemoryNoteTypeRepository,
    InMemoryPassageRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
    InMemoryShareLinkRepository,
    InMemoryUserRepository,
)
from flashcards.application.deck_service import DeckService
from flashcards.application.library_service import LibraryService
from flashcards.application.note_service import NoteService
from flashcards.application.read_access import ReadAccess
from flashcards.application.review_service import ReviewService
from flashcards.domain.notes import NoteType
from flashcards.domain.scheduling import (
    UNDO_WINDOW,
    CardSchedule,
    DuplicateReview,
    Rating,
    ReviewLog,
    ReviewNotFound,
    ScheduleState,
    UndoRejected,
    UndoSnapshot,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ALICE = "alice"
BOB = "bob"


class FakeClock:
    def __init__(self) -> None:
        self.current = NOW

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class _Rig:
    def __init__(self, logs: InMemoryReviewLogRepository | None = None) -> None:
        self.clock = FakeClock()
        deck_repo, note_repo = InMemoryDeckRepository(), InMemoryNoteRepository()
        cards = InMemoryCardRepository()
        self.decks = DeckService(deck_repo, self.clock)
        self.notes = NoteService(self.decks, note_repo, cards, self.clock)
        self.schedules = InMemoryScheduleRepository()
        self.logs = logs or InMemoryReviewLogRepository()
        self.library = LibraryService(
            self.decks,
            self.notes,
            deck_repo,
            note_repo,
            cards,
            self.schedules,
            self.logs,
            InMemoryPassageRepository(),
            InMemoryMockExamRepository(),
            InMemoryShareLinkRepository(),
        )
        access = ReadAccess(
            InMemoryUserRepository(),
            deck_repo,
            note_repo,
            cards,
            InMemoryNoteTypeRepository(),
            InMemoryMediaRepository(),
        )
        self.reviews = ReviewService(access, self.schedules, self.logs, FsrsScheduler(), self.clock)

    async def card(self, owner: str = ALICE) -> tuple[str, str]:
        deck = await self.decks.create(owner_id=owner, name="Deck")
        note, cards = await self.notes.create_note(
            owner_id=owner,
            deck_id=deck.id,
            note_type=NoteType.BASIC,
            fields={"front": "Q", "back": "A"},
        )
        return note.id, cards[0].id

    async def answer(
        self, card_id: str, rating: Rating = Rating.GOOD, key: str | None = None
    ) -> str:
        outcome = await self.reviews.submit(
            owner_id=ALICE, card_id=card_id, rating=rating, idempotency_key=key
        )
        return outcome.review_id


@pytest.fixture
def rig() -> _Rig:
    return _Rig()


async def test_undo_of_a_new_cards_first_answer_makes_it_new_again(rig: _Rig) -> None:
    _, card = await rig.card()
    review_id = await rig.answer(card)
    rig.clock.advance(timedelta(seconds=5))

    outcome = await rig.reviews.undo(owner_id=ALICE, review_id=review_id)

    assert (outcome.review_id, outcome.card_id, outcome.schedule) == (review_id, card, None)
    assert await rig.schedules.get(ALICE, card) is None
    assert await rig.logs.list_for_owner(ALICE) == []


async def test_undo_restores_the_previous_schedule_one_step_only(rig: _Rig) -> None:
    _, card = await rig.card()
    first = await rig.answer(card, Rating.GOOD)
    after_first = await rig.schedules.get(ALICE, card)
    rig.clock.advance(timedelta(minutes=1))
    second = await rig.answer(card, Rating.AGAIN)

    outcome = await rig.reviews.undo(owner_id=ALICE, review_id=second)
    assert outcome.schedule == after_first
    assert await rig.schedules.get(ALICE, card) == after_first
    # One step only: the answer before it stays, until a new answer comes.
    with pytest.raises(UndoRejected, match="most recent"):
        await rig.reviews.undo(owner_id=ALICE, review_id=first)
    assert await rig.schedules.get(ALICE, card) == after_first


async def test_a_late_retry_of_an_undone_answer_is_refused(rig: _Rig) -> None:
    _, card = await rig.card()
    review_id = await rig.answer(card, key="k1")
    await rig.reviews.undo(owner_id=ALICE, review_id=review_id)
    with pytest.raises(UndoRejected, match="was undone"):
        await rig.answer(card, key="k1")
    assert await rig.logs.list_for_owner(ALICE) == []
    assert await rig.schedules.get(ALICE, card) is None
    # Other keys, other cards and keyless answers still go through.
    await rig.answer(card, key="k2")
    await rig.answer(card)
    assert len(await rig.logs.list_for_owner(ALICE)) == 2


async def test_deleting_the_last_answered_card_never_exposes_an_older_answer(
    rig: _Rig,
) -> None:
    _, card_a = await rig.card()
    note_b, card_b = await rig.card()
    older = await rig.answer(card_a)
    await rig.answer(card_b)
    await rig.library.delete_note(owner_id=ALICE, note_id=note_b)
    with pytest.raises(UndoRejected, match="most recent"):
        await rig.reviews.undo(owner_id=ALICE, review_id=older)


async def test_last_answer_bookkeeping_goes_with_the_account() -> None:
    logs = InMemoryReviewLogRepository()
    await logs.mark_undone(ALICE)  # nothing recorded yet: a no-op
    assert await logs.last_answer(ALICE) is None
    log = ReviewLog(id="r", owner_id=ALICE, card_id="c", rating=Rating.GOOD, reviewed_at=NOW)
    await logs.set_last_answer(log)
    await logs.delete_for_owner(ALICE)
    assert await logs.last_answer(ALICE) is None


async def test_repeating_an_undo_changes_nothing(rig: _Rig) -> None:
    _, card = await rig.card()
    first = await rig.answer(card)
    rig.clock.advance(timedelta(minutes=1))
    second = await rig.answer(card)
    await rig.reviews.undo(owner_id=ALICE, review_id=second)
    with pytest.raises(ReviewNotFound):
        await rig.reviews.undo(owner_id=ALICE, review_id=second)
    # The earlier answer was never touched by the repeat.
    assert [log.id for log in await rig.logs.list_for_owner(ALICE)] == [first]


async def test_only_the_most_recent_answer_can_be_undone(rig: _Rig) -> None:
    _, card_a = await rig.card()
    _, card_b = await rig.card()
    first = await rig.answer(card_a)
    rig.clock.advance(timedelta(seconds=1))
    await rig.answer(card_b)
    with pytest.raises(UndoRejected, match="most recent"):
        await rig.reviews.undo(owner_id=ALICE, review_id=first)


async def test_undo_window_is_bounded(rig: _Rig) -> None:
    _, card = await rig.card()
    review_id = await rig.answer(card)
    rig.clock.advance(UNDO_WINDOW + timedelta(seconds=1))
    with pytest.raises(UndoRejected, match="passed"):
        await rig.reviews.undo(owner_id=ALICE, review_id=review_id)


async def test_undo_of_someone_elses_or_an_unknown_review_is_not_found(rig: _Rig) -> None:
    _, card = await rig.card()
    review_id = await rig.answer(card)
    with pytest.raises(ReviewNotFound):
        await rig.reviews.undo(owner_id=BOB, review_id=review_id)
    with pytest.raises(ReviewNotFound):
        await rig.reviews.undo(owner_id=ALICE, review_id="missing")


async def test_a_review_without_a_snapshot_cannot_be_undone(rig: _Rig) -> None:
    _, card = await rig.card()
    legacy = ReviewLog(
        id="legacy", owner_id=ALICE, card_id=card, rating=Rating.GOOD, reviewed_at=NOW
    )
    await rig.logs.append(legacy)
    await rig.logs.set_last_answer(legacy)
    with pytest.raises(UndoRejected, match="cannot be undone"):
        await rig.reviews.undo(owner_id=ALICE, review_id="legacy")


async def test_undo_refuses_when_the_card_moved_on_since(rig: _Rig) -> None:
    _, card = await rig.card()
    review_id = await rig.answer(card)
    schedule = await rig.schedules.get(ALICE, card)
    assert schedule is not None
    # Something else (a reset, an import) rewrote the schedule after the answer.
    await rig.schedules.upsert(ALICE, replace(schedule, last_reviewed_at=NOW - timedelta(days=3)))
    with pytest.raises(UndoRejected, match="changed"):
        await rig.reviews.undo(owner_id=ALICE, review_id=review_id)
    await rig.schedules.delete(ALICE, card)
    with pytest.raises(UndoRejected, match="changed"):
        await rig.reviews.undo(owner_id=ALICE, review_id=review_id)


async def test_editing_the_card_mid_session_keeps_undo_working(rig: _Rig) -> None:
    note_id, card = await rig.card()
    review_id = await rig.answer(card)
    await rig.library.update_note(
        owner_id=ALICE, note_id=note_id, fields={"front": "Q!", "back": "A"}
    )
    # The card keeps its id and schedule across the edit, so undo still applies.
    outcome = await rig.reviews.undo(owner_id=ALICE, review_id=review_id)
    assert outcome.card_id == card


class _LosingDeleteLogs(InMemoryReviewLogRepository):
    """Another undo removed the log first (only possible without the owner lock)."""

    async def try_delete(self, log_id: str) -> bool:
        await super().try_delete(log_id)
        return False


async def test_losing_a_delete_race_reports_not_found() -> None:
    rig = _Rig(_LosingDeleteLogs())
    _, card = await rig.card()
    review_id = await rig.answer(card)
    with pytest.raises(ReviewNotFound):
        await rig.reviews.undo(owner_id=ALICE, review_id=review_id)
    assert await rig.schedules.get(ALICE, card) is not None  # nothing restored by the loser


class _VanishingDuplicateLogs(InMemoryReviewLogRepository):
    """The key was recorded and then undone between the two steps of a retry."""

    async def append(self, log: ReviewLog) -> None:
        raise DuplicateReview(log.idempotency_key)


async def test_retry_of_an_undone_answer_is_not_found() -> None:
    rig = _Rig(_VanishingDuplicateLogs())
    _, card = await rig.card()
    with pytest.raises(ReviewNotFound):
        await rig.answer(card, key="k")


def _snapshot_log(undo: UndoSnapshot | None) -> ReviewLog:
    return ReviewLog(
        id="r1",
        owner_id=ALICE,
        card_id="c1",
        rating=Rating.HARD,
        reviewed_at=NOW,
        idempotency_key="k",
        undo=undo,
    )


def test_undo_snapshots_round_trip_through_rows() -> None:
    prior = CardSchedule(
        card_id="c1",
        state=ScheduleState.RELEARNING,
        step=0,
        stability=2.5,
        difficulty=6.1,
        due_at=NOW - timedelta(hours=1),
        last_reviewed_at=NOW - timedelta(days=2),
    )
    for undo in (None, UndoSnapshot(None), UndoSnapshot(prior)):
        log = _snapshot_log(undo)
        assert review_log_from_row(review_log_to_row(log)) == log


def test_a_half_written_snapshot_is_not_undoable() -> None:
    row = review_log_to_row(_snapshot_log(UndoSnapshot(None)))
    row.undo_kind = "schedule"  # but no prior_* columns
    assert review_log_from_row(row).undo is None
    row.undo_kind = "something-else"
    assert review_log_from_row(row).undo is None


# HTTP


def _deck_with_cards(client: TestClient, count: int) -> list[str]:
    deck_id = str(client.post("/decks", json={"name": "Bio"}).json()["id"])
    created = [
        client.post(
            f"/decks/{deck_id}/notes",
            json={"note_type": "basic", "fields": {"front": f"Q{n}", "back": "A"}},
        )
        for n in range(count)
    ]
    return [str(response.json()[0]["id"]) for response in created]


def _second_user(app: FastAPI, admin_client: TestClient) -> TestClient:
    token = admin_client.post("/admin/invites", json={"role": "member"}).json()["token"]
    bob = TestClient(app)
    bob.post(
        "/auth/signup",
        json={"token": token, "email": "bob@example.com", "password": "correct-horse-battery"},
    )
    return bob


def test_undo_requires_auth(client: TestClient) -> None:
    assert client.post("/reviews/x/undo").status_code == 401


def test_undo_last_answer_over_http(app: FastAPI, admin_client: TestClient) -> None:
    a, b = _deck_with_cards(admin_client, 2)
    older = admin_client.post(f"/cards/{a}/review", json={"rating": 3}).json()["review_id"]
    latest = admin_client.post(f"/cards/{b}/review", json={"rating": 3}).json()["review_id"]

    refused = admin_client.post(f"/reviews/{older}/undo")
    assert refused.status_code == 409
    assert "most recent" in refused.json()["detail"]
    bob = _second_user(app, admin_client)
    assert bob.post(f"/reviews/{latest}/undo").status_code == 404

    undone = admin_client.post(f"/reviews/{latest}/undo")
    assert undone.status_code == 200
    body = undone.json()
    assert (body["review_id"], body["card_id"], body["schedule"]) == (latest, b, None)
    assert admin_client.post(f"/reviews/{latest}/undo").status_code == 404  # repeat: no-op
    assert undone.json()["daily_goal"]["reviews"] == 1  # the undone answer no longer counts
    # One step only: the older answer stays.
    assert admin_client.post(f"/reviews/{older}/undo").status_code == 409
    queue = admin_client.get(f"/decks/{_deck_of(admin_client, a)}/review").json()
    assert [card["id"] for card in queue] == [b]  # b is new again; a is scheduled


def _deck_of(client: TestClient, card_id: str) -> str:
    return str(client.get(f"/cards/{card_id}").json()["deck_id"])


def test_undo_restores_a_previous_schedule(admin_client: TestClient) -> None:
    (card,) = _deck_with_cards(admin_client, 1)
    first = admin_client.post(f"/cards/{card}/review", json={"rating": 4}).json()
    second = admin_client.post(f"/cards/{card}/review", json={"rating": 1}).json()
    restored = admin_client.post(f"/reviews/{second['review_id']}/undo").json()["schedule"]
    assert restored["due_at"] == first["due_at"] and restored["state"] == first["state"]


def test_a_retried_answer_returns_the_original_review_id(admin_client: TestClient) -> None:
    (card,) = _deck_with_cards(admin_client, 1)
    body = {"rating": 3, "idempotency_key": "k1"}
    first = admin_client.post(f"/cards/{card}/review", json=body).json()
    again = admin_client.post(f"/cards/{card}/review", json=body).json()
    assert first["review_id"] == again["review_id"]
    assert admin_client.post(f"/reviews/{first['review_id']}/undo").status_code == 200


async def test_undo_of_a_first_answer_leaves_another_users_schedule(rig: _Rig) -> None:
    _, card = await rig.card()
    theirs = CardSchedule(
        card_id=card,
        state=ScheduleState.REVIEW,
        step=None,
        stability=4.0,
        difficulty=5.0,
        due_at=NOW + timedelta(days=4),
        last_reviewed_at=NOW,
    )
    await rig.schedules.upsert(BOB, theirs)  # another reader's own progress on the card
    review_id = await rig.answer(card)

    await rig.reviews.undo(owner_id=ALICE, review_id=review_id)

    assert await rig.schedules.get(ALICE, card) is None
    assert await rig.schedules.get(BOB, card) == theirs
