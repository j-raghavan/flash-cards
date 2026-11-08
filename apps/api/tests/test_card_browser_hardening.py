# SPDX-License-Identifier: AGPL-3.0-only
"""Review follow-ups for the card browser: integrity, validation and export."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
from flashcards.application.deck_service import DeckService
from flashcards.application.library_service import LibraryService
from flashcards.application.note_service import NoteService
from flashcards.domain.browser import card_proficiency
from flashcards.domain.notes import MAX_FIELD_NAME_LEN, Card, InvalidNote, NoteType
from flashcards.domain.scheduling import CardSchedule, ScheduleState
from flashcards.domain.tags import InvalidTags, normalize_tags, sanitize_tags

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ALICE = "alice"
PASSWORD = "correct-horse-battery"


class _Clock:
    def now(self) -> datetime:
        return NOW


@pytest.mark.parametrize(
    "bad", ["::", "a::", "::a", "a::::b", "a\x00b", "zero​width", "bidi‮flip"]
)
def test_tags_with_empty_levels_or_invisible_characters_are_refused(bad: str) -> None:
    with pytest.raises(InvalidTags, match="printable characters") as caught:
        normalize_tags([bad])
    assert repr(bad) not in str(caught.value)  # the input is never echoed back
    assert sanitize_tags([bad, "ok::fine"]) == ("ok::fine",)


def test_field_names_are_bounded() -> None:
    from flashcards.domain.notes import card_ordinals

    long_name = (("front", "q"), ("back", "a"), ("x" * (MAX_FIELD_NAME_LEN + 1), "v"))
    with pytest.raises(InvalidNote, match="field names"):
        card_ordinals(NoteType.BASIC, long_name)
    with pytest.raises(InvalidNote, match="field names"):
        card_ordinals(NoteType.BASIC, (("front", "q"), ("back", "a"), ("", "v")))


def test_a_corrupt_schedule_does_not_break_proficiency() -> None:
    broken = CardSchedule(
        card_id="c", state=ScheduleState.REVIEW, step=None, stability=0.0, difficulty=5.0,
        due_at=NOW, last_reviewed_at=NOW,
    )
    assert card_proficiency(broken, NOW) == 0


def test_bury_times_are_stored_in_utc() -> None:
    card = Card(id="c", owner_id=ALICE, deck_id="d", note_id="n", ordinal=0, created_at=NOW)
    india = timezone(timedelta(hours=5, minutes=30))
    until = (NOW + timedelta(hours=10)).astimezone(india)
    buried = card.buried(NOW, until)
    assert buried.buried_until == until and buried.buried_until is not None
    assert buried.buried_until.utcoffset() == timedelta(0)


async def test_a_notes_cards_are_found_wherever_the_note_points() -> None:
    clock = _Clock()
    cards, note_repo, deck_repo = (
        InMemoryCardRepository(), InMemoryNoteRepository(), InMemoryDeckRepository()
    )
    schedules, logs = InMemoryScheduleRepository(), InMemoryReviewLogRepository()
    decks = DeckService(deck_repo, clock)
    notes = NoteService(decks, note_repo, cards, clock)
    library = LibraryService(
        decks, notes, deck_repo, note_repo, cards, schedules, logs,
        InMemoryPassageRepository(), InMemoryMockExamRepository(), InMemoryShareLinkRepository(),
    )
    first = await decks.create(owner_id=ALICE, name="A")
    second = await decks.create(owner_id=ALICE, name="B")
    note, created = await notes.create_note(
        owner_id=ALICE, deck_id=first.id, note_type=NoteType.CLOZE,
        fields={"text": "{{c1::x}} {{c2::y}}"},
    )
    # Even if a note and its cards ever disagree about the deck, editing the note
    # finds its cards instead of creating duplicates, and deleting removes them.
    await note_repo.update(note.moved_to(second.id))
    assert [c.id for c in await notes.cards_of(note)] == [c.id for c in created]
    _edited, after = await library.update_note(owner_id=ALICE, note_id=note.id, tags=["t"])
    assert [c.id for c in after] == [c.id for c in created]
    await library.delete_note(owner_id=ALICE, note_id=note.id)
    assert await cards.list_for_notes([note.id]) == []
    assert await note_repo.lock_many([note.id, "missing"]) == []


async def test_repository_batches_find_existing_rows_only() -> None:
    cards = InMemoryCardRepository()
    for cid, nid, ordinal in (("c2", "n1", 2), ("c1", "n1", 1), ("c3", "n2", 0)):
        await cards.add(
            Card(id=cid, owner_id=ALICE, deck_id="d", note_id=nid, ordinal=ordinal,
                 created_at=NOW)
        )
    assert [c.id for c in await cards.get_many(["c3", "missing", "c1", "c3"])] == ["c3", "c1"]
    assert [c.id for c in await cards.list_for_notes(["n2", "n1"])] == ["c1", "c2", "c3"]


def _signed_in(app: FastAPI, seed_invite: Callable[..., str]) -> tuple[TestClient, list[str]]:
    client = TestClient(app)
    body = {"token": seed_invite(), "email": "alice@example.com", "password": PASSWORD}
    assert client.post("/auth/signup", json=body).status_code == 201
    deck = client.post("/decks", json={"name": "Bio"}).json()
    ids = [
        client.post(
            f"/decks/{deck['id']}/notes",
            json={"note_type": "basic", "fields": {"front": f"q{i}", "back": "a"},
                  "tags": ["bio"]},
        ).json()[0]["id"]
        for i in range(2)
    ]
    return client, ids


def test_validation_over_http(app: FastAPI, seed_invite: Callable[..., str]) -> None:
    client, ids = _signed_in(app, seed_invite)
    note_id = client.get(f"/cards/{ids[0]}").json()["note_id"]

    huge = client.patch(f"/notes/{note_id}", json={"tags": ["x" * 5_000_000]})
    assert huge.status_code == 422 and len(huge.content) < 5_000  # the value is not echoed
    assert huge.json()["detail"][0]["loc"] == ["body", "tags", 0]
    secret = client.post("/auth/login", json={"email": "a@example.com", "password": 12345})
    assert secret.status_code == 422 and "12345" not in secret.text
    assert client.patch(f"/notes/{note_id}", json={"tags": ["a::"]}).status_code == 422
    long_name = {"front": "q", "back": "a", "n" * 65: "v"}
    assert client.patch(f"/notes/{note_id}", json={"fields": long_name}).status_code == 422

    assert client.get("/cards", params={"tag": ""}).json()["total"] == 2  # no filter
    inverted = client.get("/cards", params={"min_proficiency": 60, "max_proficiency": 40})
    assert inverted.status_code == 422
    assert client.get("/cards", params={"offset": 100_001}).status_code == 422

    no_flag = client.post("/cards/bulk", json={"card_ids": ids, "action": "flag"})
    assert no_flag.status_code == 422
    cleared = client.post("/cards/bulk", json={"card_ids": ids, "action": "flag", "flag": 0})
    assert cleared.json() == {"cards": 0, "notes": 0}  # nothing was flagged
    first = client.post("/cards/bulk", json={"card_ids": ids, "action": "suspend"}).json()
    again = client.post("/cards/bulk", json={"card_ids": ids, "action": "suspend"}).json()
    assert first["cards"] == 2 and again["cards"] == 0


def test_export_includes_tags_flags_and_bury(
    app: FastAPI, seed_invite: Callable[..., str]
) -> None:
    client, ids = _signed_in(app, seed_invite)
    client.post("/cards/bulk", json={"card_ids": ids[:1], "action": "flag", "flag": 3})
    client.post("/cards/bulk", json={"card_ids": ids[:1], "action": "bury"})
    export = client.get("/account/export").json()
    assert {tuple(n["tags"]) for n in export["notes"]} == {("bio",)}
    flagged = next(c for c in export["cards"] if c["id"] == ids[0])
    assert flagged["flag"] == 3 and flagged["buried_until"] is not None
