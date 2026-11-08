# SPDX-License-Identifier: AGPL-3.0-only
"""Bulk card actions (F-DECK-3) and burying in the review queue (F-CARD-6)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.adapters.fsrs_scheduler import FsrsScheduler
from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryMockExamRepository,
    InMemoryNoteRepository,
    InMemoryPassageRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
    InMemoryShareLinkRepository,
)
from flashcards.application.bulk_card_service import BulkAction, BulkCardService
from flashcards.application.deck_service import DeckService
from flashcards.application.library_service import LibraryService
from flashcards.application.note_service import NoteService
from flashcards.application.review_service import ReviewService
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.notes import CardNotFound, InvalidBury, InvalidFlag, NoteType
from flashcards.domain.scheduling import Rating
from flashcards.domain.tags import MAX_TAGS_PER_NOTE, InvalidTags

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ALICE, BOB = "alice", "bob"
PASSWORD = "correct-horse-battery"


class _Clock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        return self.value


class _Rig:
    def __init__(self) -> None:
        self.clock = _Clock()
        self.cards = InMemoryCardRepository()
        self.note_repo = InMemoryNoteRepository()
        schedules, logs = InMemoryScheduleRepository(), InMemoryReviewLogRepository()
        deck_repo = InMemoryDeckRepository()
        self.decks = DeckService(deck_repo, self.clock)
        self.notes = NoteService(self.decks, self.note_repo, self.cards, self.clock)
        self.reviews = ReviewService(self.notes, schedules, logs, FsrsScheduler(), self.clock)
        library = LibraryService(
            self.decks, self.notes, deck_repo, self.note_repo, self.cards, schedules, logs,
            InMemoryPassageRepository(), InMemoryMockExamRepository(),
            InMemoryShareLinkRepository(),
        )
        self.bulk = BulkCardService(self.decks, library, self.note_repo, self.cards, self.clock)

    async def setup(self) -> tuple[str, str, list[str], str]:
        """Two decks; a two-card cloze note and a basic note in the first."""
        source = await self.decks.create(owner_id=ALICE, name="Source")
        target = await self.decks.create(owner_id=ALICE, name="Target")
        _cloze, cloze_cards = await self.notes.create_note(
            owner_id=ALICE, deck_id=source.id, note_type=NoteType.CLOZE,
            fields={"text": "{{c1::a}} {{c2::b}}"}, tags=("keep",),
        )
        _basic, basic_cards = await self.notes.create_note(
            owner_id=ALICE, deck_id=source.id, note_type=NoteType.BASIC,
            fields={"front": "q", "back": "a"},
        )
        return source.id, target.id, [c.id for c in cloze_cards], basic_cards[0].id

    async def card(self, card_id: str) -> tuple[str, int, bool, bool]:
        card = await self.notes.get_card(owner_id=ALICE, card_id=card_id)
        return card.deck_id, card.flag, card.is_suspended, card.is_buried(self.clock.value)


async def test_move_takes_every_card_of_the_note_along() -> None:
    rig = _Rig()
    _source, target, cloze, basic = await rig.setup()

    result = await rig.bulk.apply(
        owner_id=ALICE, card_ids=[cloze[0]], action=BulkAction.MOVE, deck_id=target
    )

    assert (result.cards, result.notes) == (2, 1)  # the note's other card moves too
    assert [(await rig.card(cid))[0] for cid in cloze] == [target, target]
    note = await rig.notes.get_note(
        owner_id=ALICE, note_id=(await rig.notes.get_card(owner_id=ALICE, card_id=cloze[0])).note_id
    )
    assert note.deck_id == target
    assert (await rig.card(basic))[0] != target
    with pytest.raises(DeckNotFound):
        await rig.bulk.apply(
            owner_id=ALICE, card_ids=[basic], action=BulkAction.MOVE, deck_id="missing"
        )
    with pytest.raises(ValueError, match="deck_id"):
        await rig.bulk.apply(owner_id=ALICE, card_ids=[basic], action=BulkAction.MOVE)


async def test_delete_removes_whole_notes() -> None:
    rig = _Rig()
    source, _target, cloze, basic = await rig.setup()
    result = await rig.bulk.apply(
        owner_id=ALICE, card_ids=[cloze[1], cloze[0], cloze[1]], action=BulkAction.DELETE
    )
    assert (result.cards, result.notes) == (2, 1)
    assert [c.id for c in await rig.notes.list_cards(owner_id=ALICE, deck_id=source)] == [basic]


async def test_adding_and_removing_tags_checks_every_note_first() -> None:
    rig = _Rig()
    _source, _target, cloze, basic = await rig.setup()
    added = await rig.bulk.apply(
        owner_id=ALICE, card_ids=[cloze[0], basic], action=BulkAction.ADD_TAGS, tags=["Exam"]
    )
    assert (added.cards, added.notes) == (3, 2)
    basic_note = (await rig.notes.get_card(owner_id=ALICE, card_id=basic)).note_id
    assert (await rig.notes.get_note(owner_id=ALICE, note_id=basic_note)).tags == ("Exam",)

    await rig.bulk.apply(
        owner_id=ALICE, card_ids=[cloze[0], basic], action=BulkAction.REMOVE_TAGS, tags=["exam"]
    )
    assert (await rig.notes.get_note(owner_id=ALICE, note_id=basic_note)).tags == ()

    # One note would exceed the tag limit, so neither note changes.
    many = [f"t{i}" for i in range(MAX_TAGS_PER_NOTE)]
    with pytest.raises(InvalidTags):
        await rig.bulk.apply(
            owner_id=ALICE, card_ids=[basic, cloze[0]], action=BulkAction.ADD_TAGS, tags=many
        )
    assert (await rig.notes.get_note(owner_id=ALICE, note_id=basic_note)).tags == ()


async def test_card_level_actions() -> None:
    rig = _Rig()
    _source, _target, cloze, basic = await rig.setup()
    ids = [cloze[0], basic]
    suspended_now = await rig.bulk.apply(owner_id=ALICE, card_ids=ids, action=BulkAction.SUSPEND)
    assert suspended_now.cards == 2
    await rig.bulk.apply(owner_id=ALICE, card_ids=ids, action=BulkAction.SUSPEND)  # idempotent
    assert (await rig.card(basic))[2]
    await rig.bulk.apply(owner_id=ALICE, card_ids=ids, action=BulkAction.UNSUSPEND)
    await rig.bulk.apply(owner_id=ALICE, card_ids=ids, action=BulkAction.FLAG, flag=5)
    await rig.bulk.apply(owner_id=ALICE, card_ids=ids, action=BulkAction.BURY)
    _deck, flag, suspended, buried = await rig.card(basic)
    assert (flag, suspended, buried) == (5, False, True)
    assert not (await rig.card(cloze[1]))[3]  # an unselected card is untouched
    await rig.bulk.apply(owner_id=ALICE, card_ids=ids, action=BulkAction.UNBURY)
    assert not (await rig.card(basic))[3]


async def test_nothing_changes_when_any_id_or_argument_is_bad() -> None:
    rig = _Rig()
    _source, _target, cloze, basic = await rig.setup()
    bob_deck = await rig.decks.create(owner_id=BOB, name="Bob")
    _n, bob_cards = await rig.notes.create_note(
        owner_id=BOB, deck_id=bob_deck.id, note_type=NoteType.BASIC,
        fields={"front": "q", "back": "a"},
    )
    for foreign in ("missing", bob_cards[0].id):
        with pytest.raises(CardNotFound):
            await rig.bulk.apply(
                owner_id=ALICE, card_ids=[basic, foreign], action=BulkAction.SUSPEND
            )
    with pytest.raises(InvalidFlag):
        await rig.bulk.apply(owner_id=ALICE, card_ids=[basic], action=BulkAction.FLAG, flag=9)
    with pytest.raises(InvalidBury):
        await rig.bulk.apply(
            owner_id=ALICE, card_ids=[basic, cloze[0]], action=BulkAction.BURY,
            until=NOW + timedelta(days=30),
        )
    _deck, flag, suspended, buried = await rig.card(basic)
    assert (flag, suspended, buried) == (0, False, False)
    assert not (await rig.card(cloze[0]))[3]  # the valid card in the batch was not buried


async def test_buried_cards_leave_the_queue_until_the_bury_ends() -> None:
    rig = _Rig()
    source, _target, cloze, basic = await rig.setup()
    await rig.bulk.apply(
        owner_id=ALICE, card_ids=[basic], action=BulkAction.BURY, until=NOW + timedelta(hours=8)
    )
    queue = await rig.reviews.due_queue(owner_id=ALICE, deck_id=source)
    assert [card.id for card in queue] == cloze
    stats = await rig.reviews.deck_stats(owner_id=ALICE, deck_id=source)
    assert stats.due == 2 and stats.new == 3  # still new, just not due today
    await rig.reviews.submit(owner_id=ALICE, card_id=cloze[0], rating=Rating.AGAIN)
    await rig.bulk.apply(owner_id=ALICE, card_ids=[cloze[0]], action=BulkAction.BURY)
    assert (await rig.reviews.deck_stats(owner_id=ALICE, deck_id=source)).due == 1

    rig.clock.value = NOW + timedelta(hours=8)
    assert basic in [c.id for c in await rig.reviews.due_queue(owner_id=ALICE, deck_id=source)]


def _signed_in(app: FastAPI, seed_invite: Callable[..., str]) -> TestClient:
    client = TestClient(app)
    body = {"token": seed_invite(), "email": "alice@example.com", "password": PASSWORD}
    assert client.post("/auth/signup", json=body).status_code == 201
    return client


def test_bulk_actions_over_http(app: FastAPI, seed_invite: Callable[..., str]) -> None:
    client = _signed_in(app, seed_invite)
    source = client.post("/decks", json={"name": "Source"}).json()
    target = client.post("/decks", json={"name": "Target"}).json()
    ids = [
        client.post(
            f"/decks/{source['id']}/notes",
            json={"note_type": "basic", "fields": {"front": f"q{i}", "back": "a"}},
        ).json()[0]["id"]
        for i in range(3)
    ]

    moved = client.post(
        "/cards/bulk", json={"card_ids": ids[:2], "action": "move", "deck_id": target["id"]}
    )
    assert moved.status_code == 200 and moved.json() == {"cards": 2, "notes": 2}
    in_target = client.get("/cards", params={"deck_id": target["id"]}).json()
    assert in_target["total"] == 2

    until = (datetime.now(UTC) + timedelta(hours=10)).isoformat()
    buried = client.post("/cards/bulk", json={"card_ids": ids, "action": "bury", "until": until})
    assert buried.json() == {"cards": 3, "notes": 0}
    assert client.get("/cards", params={"state": "buried"}).json()["total"] == 3
    assert client.get(f"/decks/{source['id']}/review").json() == []  # all buried

    flagged = client.post("/cards/bulk", json={"card_ids": ids, "action": "flag", "flag": 4})
    assert flagged.status_code == 200
    assert client.get("/cards", params={"flag": 4}).json()["total"] == 3

    for bad in (
        {"card_ids": [], "action": "suspend"},
        {"card_ids": ids, "action": "move"},
        {"card_ids": ids, "action": "add_tags"},
        {"card_ids": ids, "action": "flag", "flag": 8},
        {"card_ids": ids, "action": "bury", "until": "2026-11-09T00:00:00"},  # no time zone
        {"card_ids": ids, "action": "explode"},
        {"card_ids": ["x"] * 1001, "action": "suspend"},
    ):
        assert client.post("/cards/bulk", json=bad).status_code == 422
    assert client.post(
        "/cards/bulk", json={"card_ids": ["missing"], "action": "suspend"}
    ).status_code == 404
    too_long = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    assert client.post(
        "/cards/bulk", json={"card_ids": ids, "action": "bury", "until": too_long}
    ).status_code == 422
