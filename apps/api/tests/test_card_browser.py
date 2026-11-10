# SPDX-License-Identifier: AGPL-3.0-only
"""The card browser: filters, search, sort and paging (F-DECK-2, ADR 0011)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryNoteRepository,
    InMemoryScheduleRepository,
)
from flashcards.application.card_browser_service import CardBrowserService
from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.domain.browser import (
    PREVIEW_CHARS,
    BrowserQuery,
    CardState,
    SortKey,
    card_proficiency,
    card_state,
    preview,
)
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.notes import Card, Note, NoteType
from flashcards.domain.scheduling import CardSchedule, ScheduleState

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
Q = BrowserQuery
BIO = ["new card", "learning card", "Mitochondria review"]
ALICE, BOB = "alice", "bob"
PASSWORD = "correct-horse-battery"


class _Clock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        return self.value


def _schedule(card_id: str, state: ScheduleState, *, days_ago: float = 0) -> CardSchedule:
    reviewed = NOW - timedelta(days=days_ago)
    return CardSchedule(
        card_id=card_id, state=state, step=None, stability=10.0, difficulty=5.0,
        due_at=reviewed + timedelta(days=10), last_reviewed_at=reviewed,
    )


def _card(**changes: object) -> Card:
    card = Card(id="c", owner_id=ALICE, deck_id="d", note_id="n", ordinal=0, created_at=NOW)
    if changes.get("suspended"):
        card = card.suspended(NOW)
    if changes.get("buried"):
        card = card.buried(NOW)
    return card


def test_state_prefers_suspended_then_buried_then_the_schedule() -> None:
    review = _schedule("c", ScheduleState.REVIEW)
    assert card_state(_card(suspended=True, buried=True), review, NOW) is CardState.SUSPENDED
    assert card_state(_card(buried=True), review, NOW) is CardState.BURIED
    assert card_state(_card(buried=True), review, NOW + timedelta(days=2)) is CardState.REVIEW
    assert card_state(_card(), None, NOW) is CardState.NEW
    for learning in (ScheduleState.LEARNING, ScheduleState.RELEARNING):
        assert card_state(_card(), _schedule("c", learning), NOW) is CardState.LEARNING


def test_proficiency_is_the_chance_of_recall_now() -> None:
    assert card_proficiency(None, NOW) == 0
    assert card_proficiency(_schedule("c", ScheduleState.REVIEW), NOW) == 100
    fading = card_proficiency(_schedule("c", ScheduleState.REVIEW, days_ago=30), NOW)
    assert 0 < fading < 90
    unreviewed = CardSchedule(
        card_id="c", state=ScheduleState.LEARNING, step=0, stability=None, difficulty=None,
        due_at=NOW, last_reviewed_at=None,
    )
    assert card_proficiency(unreviewed, NOW) == 0


def test_preview_is_the_first_field_collapsed_and_trimmed() -> None:
    def note(fields: tuple[tuple[str, str], ...]) -> Note:
        return Note(id="n", owner_id=ALICE, deck_id="d", note_type=NoteType.BASIC,
                    fields=fields, created_at=NOW)

    assert preview(note((("front", "  two\n words "), ("back", "x")))) == "two words"
    long = preview(note((("front", "x" * 500),)))
    assert len(long) == PREVIEW_CHARS + 1 and long.endswith("…")
    assert preview(note(())) == ""


class _Rig:
    def __init__(self) -> None:
        self.clock = _Clock()
        self.cards = InMemoryCardRepository()
        self.schedules = InMemoryScheduleRepository()
        notes = InMemoryNoteRepository()
        self.decks = DeckService(InMemoryDeckRepository(), self.clock)
        self.notes = NoteService(self.decks, notes, self.cards, self.clock)
        self.browser = CardBrowserService(
            self.decks, notes, self.cards, self.schedules, self.clock
        )

    async def add(
        self, deck_id: str, front: str, *, tags: tuple[str, ...] = (), owner: str = ALICE
    ) -> Card:
        self.clock.value += timedelta(seconds=1)  # distinct creation times
        _note, cards = await self.notes.create_note(
            owner_id=owner, deck_id=deck_id, note_type=NoteType.BASIC,
            fields={"front": front, "back": "answer"}, tags=tags,
        )
        return cards[0]

    async def search(
        self,
        query: BrowserQuery | None = None,
        *,
        deck_id: str | None = None,
        include_subdecks: bool = True,
    ) -> list[str]:
        page = await self.browser.search(
            owner_id=ALICE, query=query or BrowserQuery(), deck_id=deck_id,
            include_subdecks=include_subdecks,
        )
        return [row.preview for row in page.rows]


async def _library(rig: _Rig) -> dict[str, str]:
    bio = await rig.decks.create(owner_id=ALICE, name="Biology")
    cells = await rig.decks.create(owner_id=ALICE, name="Cells", parent_id=bio.id)
    chem = await rig.decks.create(owner_id=ALICE, name="Chemistry")
    new = await rig.add(bio.id, "new card", tags=("bio",))
    learning = await rig.add(cells.id, "learning card", tags=("bio::cells",))
    review = await rig.add(cells.id, "Mitochondria review", tags=("Bio::Cells", "exam"))
    suspended = await rig.add(chem.id, "suspended card", tags=("chem",))
    buried = await rig.add(chem.id, "buried card")
    await rig.cards.update(suspended.suspended(NOW))
    await rig.cards.update(buried.buried(NOW).with_flag(2))
    await rig.cards.update(review.with_flag(2))
    await rig.schedules.upsert(ALICE, _schedule(learning.id, ScheduleState.LEARNING))
    await rig.schedules.upsert(ALICE, _schedule(review.id, ScheduleState.REVIEW, days_ago=30))
    bob_deck = await rig.decks.create(owner_id=BOB, name="Bob's")
    await rig.add(bob_deck.id, "bob's card", owner=BOB)
    _ = new
    return {"bio": bio.id, "cells": cells.id, "chem": chem.id}


async def test_filters_combine_and_never_show_other_users_cards() -> None:
    rig = _Rig()
    decks = await _library(rig)
    everything = await rig.search()
    assert everything == [
        "new card", "learning card", "Mitochondria review", "suspended card", "buried card"
    ]
    assert await rig.search(Q(states=frozenset({CardState.NEW}))) == ["new card"]
    hidden = frozenset({CardState.SUSPENDED, CardState.BURIED})
    assert await rig.search(Q(states=hidden)) == ["suspended card", "buried card"]
    assert await rig.search(Q(tag="BIO")) == BIO
    assert await rig.search(Q(tag="bio::cells")) == BIO[1:]
    assert await rig.search(Q(flag=2)) == ["Mitochondria review", "buried card"]
    assert await rig.search(Q(text="MITOCHONDRIA")) == ["Mitochondria review"]
    assert await rig.search(Q(text="answer", tag="exam")) == ["Mitochondria review"]
    assert await rig.search(Q(min_proficiency=1, max_proficiency=99)) == [BIO[2]]
    assert await rig.search(Q(min_proficiency=100)) == ["learning card"]
    assert await rig.search(deck_id=decks["bio"]) == BIO
    assert await rig.search(deck_id=decks["bio"], include_subdecks=False) == ["new card"]
    with pytest.raises(DeckNotFound):
        await rig.browser.search(owner_id=BOB, query=BrowserQuery(), deck_id=decks["bio"])


async def test_sorting_and_paging() -> None:
    rig = _Rig()
    await _library(rig)
    assert await rig.search(Q(sort=SortKey.CREATED, descending=True)) == [
        "buried card", "suspended card", "Mitochondria review", "learning card", "new card"
    ]
    by_proficiency = await rig.search(Q(sort=SortKey.PROFICIENCY, descending=True))
    assert by_proficiency[0] == "learning card" and by_proficiency[1] == "Mitochondria review"
    # Biology, then Cells, then Chemistry; creation order within a deck.
    assert await rig.search(Q(sort=SortKey.DECK, descending=True)) == [
        "buried card", "suspended card", "Mitochondria review", "learning card", "new card"
    ]
    # New cards count as due from creation; the reviewed card is due in the past.
    by_due = await rig.search(Q(sort=SortKey.DUE))
    assert by_due[0] == "Mitochondria review"

    page = await rig.browser.search(owner_id=ALICE, query=BrowserQuery(limit=2, offset=2))
    assert page.total == 5 and [r.preview for r in page.rows] == [
        "Mitochondria review", "suspended card"
    ]
    beyond = await rig.browser.search(owner_id=ALICE, query=BrowserQuery(offset=50))
    assert beyond.total == 5 and beyond.rows == ()


def _signed_in(app: FastAPI, seed_invite: Callable[..., str]) -> TestClient:
    client = TestClient(app)
    body = {"token": seed_invite(), "email": "alice@example.com", "password": PASSWORD}
    assert client.post("/auth/signup", json=body).status_code == 201
    return client


def test_browser_over_http(app: FastAPI, seed_invite: Callable[..., str]) -> None:
    client = _signed_in(app, seed_invite)
    deck = client.post("/decks", json={"name": "Bio"}).json()
    for front, tags in (("alpha", ["x"]), ("beta", ["y"]), ("gamma", ["x::z"])):
        client.post(
            f"/decks/{deck['id']}/notes",
            json={"note_type": "basic", "fields": {"front": front, "back": "b"}, "tags": tags},
        )

    page = client.get("/cards", params={"tag": "x", "sort": "created", "order": "desc"}).json()
    assert page["total"] == 2 and [c["preview"] for c in page["cards"]] == ["gamma", "alpha"]
    row = page["cards"][0]
    assert row["deck_name"] == "Bio" and row["state"] == "new" and row["proficiency"] == 0
    assert row["tags"] == ["x::z"] and row["due_at"] is None and row["flag"] == 0

    states = client.get("/cards", params=[("state", "new"), ("state", "review")]).json()
    assert states["total"] == 3
    scoped = client.get("/cards", params={"deck_id": deck["id"], "q": "BET"}).json()
    assert [c["preview"] for c in scoped["cards"]] == ["beta"]
    assert client.get("/cards", params={"deck_id": "missing"}).status_code == 404
    for bad in ({"limit": 0}, {"limit": 201}, {"flag": 8}, {"state": "nope"}, {"sort": "x"}):
        assert client.get("/cards", params=bad).status_code == 422
    assert TestClient(app).get("/cards").status_code == 401
