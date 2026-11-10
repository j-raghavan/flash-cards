# SPDX-License-Identifier: AGPL-3.0-only
"""Editing a note's fields and tags (ADR 0011)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
from flashcards.domain.notes import InvalidNote, NoteNotFound, NoteType
from flashcards.domain.scheduling import Rating
from flashcards.domain.tags import InvalidTags

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ALICE = "alice"
PASSWORD = "correct-horse-battery"


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Rig:
    def __init__(self) -> None:
        clock = _Clock()
        self.cards = InMemoryCardRepository()
        self.schedules = InMemoryScheduleRepository()
        self.logs = InMemoryReviewLogRepository()
        deck_repo, note_repo = InMemoryDeckRepository(), InMemoryNoteRepository()
        self.decks = DeckService(deck_repo, clock)
        self.notes = NoteService(self.decks, note_repo, self.cards, clock)
        access = ReadAccess(
            InMemoryUserRepository(),
            deck_repo,
            note_repo,
            self.cards,
            InMemoryNoteTypeRepository(),
            InMemoryMediaRepository(),
        )
        self.reviews = ReviewService(access, self.schedules, self.logs, FsrsScheduler(), clock)
        self.library = LibraryService(
            self.decks, self.notes, deck_repo, note_repo, self.cards, self.schedules, self.logs,
            InMemoryPassageRepository(), InMemoryMockExamRepository(),
            InMemoryShareLinkRepository(),
        )

    async def cloze(self, text: str, tags: tuple[str, ...] = ()) -> tuple[str, dict[int, str]]:
        deck = await self.decks.create(owner_id=ALICE, name="Bio")
        note, cards = await self.notes.create_note(
            owner_id=ALICE, deck_id=deck.id, note_type=NoteType.CLOZE, fields={"text": text},
            tags=tags,
        )
        return note.id, {card.ordinal: card.id for card in cards}


async def test_editing_a_cloze_keeps_surviving_cards_and_their_history() -> None:
    rig = _Rig()
    note_id, before = await rig.cloze("The {{c1::nucleus}} holds {{c2::DNA}}")
    for card_id in before.values():
        await rig.reviews.submit(owner_id=ALICE, card_id=card_id, rating=Rating.GOOD)

    note, cards = await rig.library.update_note(
        owner_id=ALICE, note_id=note_id, fields={"text": "The {{c1::nucleus}} and {{c3::RNA}}"}
    )

    after = {card.ordinal: card.id for card in cards}
    assert set(after) == {1, 3} and after[1] == before[1]  # c1 kept, c2 gone, c3 new
    assert note.field("text") == "The {{c1::nucleus}} and {{c3::RNA}}"
    assert await rig.schedules.get(ALICE, before[1]) is not None  # c1's schedule survives
    assert await rig.schedules.get(ALICE, before[2]) is None  # c2's schedule and log are gone
    assert [log.card_id for log in await rig.logs.list_for_owner(ALICE)] == [before[1]]
    assert await rig.cards.get(before[2]) is None


async def test_editing_only_tags_leaves_the_cards_alone() -> None:
    rig = _Rig()
    note_id, before = await rig.cloze("{{c1::x}}", tags=("old",))
    note, cards = await rig.library.update_note(
        owner_id=ALICE, note_id=note_id, tags=["New", "new", "bio::cells"]
    )
    assert note.tags == ("bio::cells", "New") and note.field("text") == "{{c1::x}}"
    assert [card.id for card in cards] == [before[1]]


async def test_an_invalid_edit_changes_nothing() -> None:
    rig = _Rig()
    note_id, before = await rig.cloze("{{c1::x}}", tags=("keep",))
    with pytest.raises(InvalidNote):
        await rig.library.update_note(owner_id=ALICE, note_id=note_id, fields={"text": "none"})
    with pytest.raises(InvalidTags):
        await rig.library.update_note(owner_id=ALICE, note_id=note_id, tags=["two words"])
    note = await rig.notes.get_note(owner_id=ALICE, note_id=note_id)
    assert note.field("text") == "{{c1::x}}" and note.tags == ("keep",)
    assert [card.id for card in await rig.notes.cards_of(note)] == [before[1]]
    with pytest.raises(NoteNotFound):
        await rig.library.update_note(owner_id="mallory", note_id=note_id, tags=[])


def _signed_in(app: FastAPI, seed_invite: Callable[..., str]) -> TestClient:
    client = TestClient(app)
    body = {"token": seed_invite(), "email": "alice@example.com", "password": PASSWORD}
    assert client.post("/auth/signup", json=body).status_code == 201
    return client


def test_note_editing_over_http(app: FastAPI, seed_invite: Callable[..., str]) -> None:
    client = _signed_in(app, seed_invite)
    deck = client.post("/decks", json={"name": "Bio"}).json()
    created = client.post(
        f"/decks/{deck['id']}/notes",
        json={"note_type": "basic", "fields": {"front": "Q", "back": "A"}, "tags": ["b", "a"]},
    ).json()
    note_id = created[0]["note_id"]
    assert created[0]["flag"] == 0 and created[0]["buried_until"] is None

    note = client.get(f"/notes/{note_id}").json()
    assert note["tags"] == ["a", "b"] and len(note["cards"]) == 1

    edited = client.patch(
        f"/notes/{note_id}", json={"fields": {"front": "Q2", "back": "A2"}, "tags": ["exam"]}
    )
    assert edited.status_code == 200
    assert edited.json()["fields"] == {"front": "Q2", "back": "A2"}
    assert edited.json()["tags"] == ["exam"]
    detail = client.get(f"/cards/{created[0]['id']}").json()
    assert detail["tags"] == ["exam"] and detail["fields"]["front"] == "Q2"

    bad_tag = client.patch(f"/notes/{note_id}", json={"tags": ["has space"]})
    assert bad_tag.status_code == 422
    bad_note = client.patch(f"/notes/{note_id}", json={"fields": {"front": "", "back": "x"}})
    assert bad_note.status_code == 422
    assert client.patch("/notes/missing", json={"tags": []}).status_code == 404
    bad_create = client.post(
        f"/decks/{deck['id']}/notes",
        json={"note_type": "basic", "fields": {"front": "Q", "back": "A"}, "tags": ["a b"]},
    )
    assert bad_create.status_code == 422
