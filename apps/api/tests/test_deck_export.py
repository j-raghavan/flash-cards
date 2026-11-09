# SPDX-License-Identifier: AGPL-3.0-only
"""Deck export as .apkg, CSV and JSON (F-IMP-4), and the .apkg round trip (F-IMP-2)."""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.apkg import ApkgReader
from flashcards.adapters.deck_export import FileDeckExportWriter
from flashcards.adapters.fsrs_scheduler import FsrsScheduler
from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryNoteRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
)
from flashcards.application.apkg_import_service import ApkgImportService
from flashcards.application.deck_export_service import DeckExportService
from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.application.review_service import ReviewService
from flashcards.domain import deck_export
from flashcards.domain.deck_export import (
    DeckExport,
    ExportedCard,
    ExportedNote,
    ExportFormat,
    ExportTooLarge,
    export_filename,
)
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.notes import Card, Mask, Note, NoteType, Occlusion
from flashcards.domain.scheduling import Rating, ReviewLog

START = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
ALICE, BOB = "alice", "bob"


class MovableClock:
    def __init__(self) -> None:
        self.moment = START

    def now(self) -> datetime:
        return self.moment


class _World:
    """One set of in-memory repositories with every service the tests need."""

    def __init__(self) -> None:
        self.clock = MovableClock()
        self.note_repo = InMemoryNoteRepository()
        self.card_repo = InMemoryCardRepository()
        self.schedules = InMemoryScheduleRepository()
        self.logs = InMemoryReviewLogRepository()
        self.scheduler = FsrsScheduler()
        self.decks = DeckService(InMemoryDeckRepository(), self.clock)
        self.notes = NoteService(self.decks, self.note_repo, self.card_repo, self.clock)
        self.reviews = ReviewService(
            self.notes, self.schedules, self.logs, self.scheduler, self.clock
        )
        self.exporter = DeckExportService(
            self.decks,
            self.note_repo,
            self.card_repo,
            self.schedules,
            self.logs,
            FileDeckExportWriter(),
            self.clock,
        )
        self.importer = ApkgImportService(
            ApkgReader(),
            self.decks,
            self.notes,
            self.card_repo,
            self.schedules,
            self.logs,
            self.scheduler,
            self.clock,
        )

    async def export(self, deck_id: str, fmt: ExportFormat, owner: str = ALICE) -> bytes:
        exported = await self.exporter.export(owner_id=owner, deck_id=deck_id, export_format=fmt)
        return exported.data


async def _library(world: _World) -> str:
    """Alice's tree: Spanish > Verbs (::odd name), with a reviewed, a suspended and a cloze card."""
    root = await world.decks.create(owner_id=ALICE, name="Spanish")
    child = await world.decks.create(owner_id=ALICE, name="Verbs::irregular", parent_id=root.id)
    _note, (reviewed,) = await world.notes.create_note(
        owner_id=ALICE,
        deck_id=root.id,
        note_type=NoteType.BASIC,
        fields={"front": "casa", "back": "=HYPERLINK(\"x\")", "source": "book"},
        tags=["nouns", "a1::home"],
    )
    _note, (suspended,) = await world.notes.create_note(
        owner_id=ALICE,
        deck_id=child.id,
        note_type=NoteType.BASIC,
        fields={"front": "<b>ser</b>", "back": "to be"},
    )
    await world.notes.create_note(
        owner_id=ALICE,
        deck_id=child.id,
        note_type=NoteType.CLOZE,
        fields={"text": "{{c1::Soy}} de {{c2::Madrid}}", "extra": "ser"},
    )
    await world.notes.suspend_card(owner_id=ALICE, card_id=suspended.id)
    flagged = (await world.notes.get_card(owner_id=ALICE, card_id=reviewed.id)).with_flag(4)
    await world.card_repo.update(flagged)
    for days, rating in ((0, Rating.GOOD), (1, Rating.AGAIN), (2, Rating.GOOD), (6, Rating.EASY)):
        world.clock.moment = START + timedelta(days=days)
        await world.reviews.submit(owner_id=ALICE, card_id=reviewed.id, rating=rating)
    world.clock.moment = START + timedelta(days=7)
    return root.id


async def test_json_export_carries_the_whole_tree() -> None:
    world = _World()
    root_id = await _library(world)

    document = json.loads(await world.export(root_id, ExportFormat.JSON))

    assert document["format"] == "flash-cards-deck" and document["version"] == 1
    assert document["decks"] == [["Spanish"], ["Spanish", "Verbs::irregular"]]
    by_front = {
        note["fields"].get("front", note["fields"].get("text")): note for note in document["notes"]
    }
    casa = by_front["casa"]
    assert casa["deck"] == ["Spanish"] and casa["tags"] == ["a1::home", "nouns"]
    assert casa["fields"]["source"] == "book"
    (card,) = casa["cards"]
    assert card["flag"] == 4 and card["suspended"] is False
    assert [review["rating"] for review in card["reviews"]] == [3, 1, 3, 4]
    assert card["schedule"]["state"] == "review"
    assert by_front["<b>ser</b>"]["cards"][0]["suspended"] is True
    assert by_front["<b>ser</b>"]["cards"][0]["schedule"] is None
    cloze = by_front["{{c1::Soy}} de {{c2::Madrid}}"]
    assert [c["ordinal"] for c in cloze["cards"]] == [1, 2]


async def test_csv_export_neutralises_formulas() -> None:
    world = _World()
    root_id = await _library(world)

    rows = list(csv.reader(io.StringIO((await world.export(root_id, ExportFormat.CSV)).decode())))

    assert rows[0] == ["deck", "note_type", "front", "back", "text", "tags"]
    casa = next(row for row in rows if row[2] == "casa")
    assert casa == ["Spanish", "basic", "casa", "'=HYPERLINK(\"x\")", "", "a1::home nouns"]
    cloze = next(row for row in rows if row[1] == "cloze")
    assert cloze[0] == "Spanish::Verbs:irregular" and cloze[4] == "{{c1::Soy}} de {{c2::Madrid}}"


async def test_apkg_export_is_an_anki_collection() -> None:
    world = _World()
    root_id = await _library(world)

    data = await world.export(root_id, ExportFormat.APKG)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert sorted(archive.namelist()) == ["collection.anki2", "media"]
        collection = archive.read("collection.anki2")
    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    try:
        with open(path, "wb") as handle:
            handle.write(collection)
        conn = sqlite3.connect(path)
        try:
            (ver, decks_json) = conn.execute("SELECT ver, decks FROM col").fetchone()
            names = sorted(deck["name"] for deck in json.loads(decks_json).values())
            sfld, csum = conn.execute(
                "SELECT sfld, csum FROM notes WHERE flds LIKE '<b>ser%'"
            ).fetchone()
            queues = sorted(row[0] for row in conn.execute("SELECT queue FROM cards"))
            revlog_kinds = [row[0] for row in conn.execute("SELECT type FROM revlog ORDER BY id")]
        finally:
            conn.close()
    finally:
        os.unlink(path)
    assert ver == 11
    assert names == ["Default", "Spanish", "Spanish::Verbs:irregular"]
    assert sfld == "ser" and isinstance(csum, int)
    assert queues == [-1, 0, 0, 2]  # suspended, two new cloze cards, one review card
    assert revlog_kinds == [0, 1, 2, 1]  # Good, Again (a lapse), Good, Easy


async def test_apkg_round_trip_keeps_notes_state_and_schedule() -> None:
    world = _World()
    root_id = await _library(world)
    original_card = next(
        c for c in await world.card_repo.list_for_owner(ALICE) if c.flag == 4
    )
    original_schedule = await world.schedules.get(original_card.id)

    data = await world.export(root_id, ExportFormat.APKG)
    report = await world.importer.import_package(owner_id=BOB, data=data)

    assert (report.decks_created, report.notes_imported, report.notes_skipped) == (2, 3, 0)
    assert report.reviews_imported == 4 and report.cards_scheduled == 1
    bob_decks = {deck.name: deck for deck in await world.decks.list_decks(BOB)}
    assert bob_decks["Verbs:irregular"].parent_id == bob_decks["Spanish"].id
    bob_cards = await world.card_repo.list_for_owner(BOB)
    assert sorted((c.ordinal, c.is_suspended, c.flag) for c in bob_cards) == [
        (0, False, 4),
        (0, True, 0),
        (1, False, 0),
        (2, False, 0),
    ]
    reviewed = next(c for c in bob_cards if c.flag == 4)
    schedule = await world.schedules.get(reviewed.id)
    assert original_schedule is not None and schedule is not None
    # Replaying the same history gives the same schedule (to the millisecond).
    assert schedule.state is original_schedule.state
    assert schedule.stability == pytest.approx(original_schedule.stability)
    assert schedule.difficulty == pytest.approx(original_schedule.difficulty)
    assert abs(schedule.due_at - original_schedule.due_at) < timedelta(seconds=1)
    note = await world.notes.get_note(owner_id=BOB, note_id=reviewed.note_id)
    assert note.fields == (("front", "casa"), ("back", "=HYPERLINK(\"x\")"))
    assert note.tags == ("a1::home", "nouns")


async def test_export_is_limited_to_the_owner() -> None:
    world = _World()
    root_id = await _library(world)

    with pytest.raises(DeckNotFound):
        await world.export(root_id, ExportFormat.JSON, owner=BOB)

    # Even a row that claims Alice's deck but is Bob's never leaves in her export.
    await world.note_repo.add(
        Note(
            id="intruder",
            owner_id=BOB,
            deck_id=root_id,
            note_type=NoteType.BASIC,
            fields=(("front", "secret"), ("back", "bob's")),
            created_at=START,
        )
    )
    document = json.loads(await world.export(root_id, ExportFormat.JSON))
    assert all(note["fields"].get("front") != "secret" for note in document["notes"])


async def test_custom_and_occlusion_notes_travel_in_json_only() -> None:
    world = _World()
    root_id = await _library(world)
    mask = Mask(id=1, left=0.1, top=0.2, width=0.3, height=0.4)
    occlusion = Occlusion(image_id="img1", masks=(mask,))
    for note in (
        Note(id="io", owner_id=ALICE, deck_id=root_id, note_type=NoteType.IMAGE_OCCLUSION,
             fields=(), created_at=START, occlusion=occlusion),
        Note(id="cu", owner_id=ALICE, deck_id=root_id, note_type=NoteType.CUSTOM,
             fields=(("Word", "gato"),), created_at=START, note_type_id="nt1"),
    ):
        await world.note_repo.add(note)
        await world.card_repo.add(
            Card(id=f"{note.id}-c", owner_id=ALICE, deck_id=root_id, note_id=note.id,
                 ordinal=1, created_at=START)
        )

    as_json = await world.exporter.export(
        owner_id=ALICE, deck_id=root_id, export_format=ExportFormat.JSON
    )
    document = json.loads(as_json.data)
    by_type = {note["note_type"]: note for note in document["notes"]}
    assert as_json.omitted_notes == 0
    assert by_type["image_occlusion"]["occlusion"] == {
        "image_id": "img1",
        "masks": [{"id": 1, "left": 0.1, "top": 0.2, "width": 0.3, "height": 0.4}],
    }
    assert by_type["custom"]["note_type_id"] == "nt1"
    assert "occlusion" not in by_type["custom"] and "note_type_id" not in by_type["basic"]

    for fmt in (ExportFormat.APKG, ExportFormat.CSV):
        exported = await world.exporter.export(owner_id=ALICE, deck_id=root_id, export_format=fmt)
        assert exported.omitted_notes == 2
    package = ApkgReader().read(
        (await world.exporter.export(
            owner_id=ALICE, deck_id=root_id, export_format=ExportFormat.APKG
        )).data
    )
    assert len(package.notes) == 3  # the Basic and Cloze notes only


async def test_subdeck_export_starts_at_that_deck() -> None:
    world = _World()
    root_id = await _library(world)
    child = next(d for d in await world.decks.list_decks(ALICE) if d.parent_id == root_id)

    exported = await world.exporter.export(
        owner_id=ALICE, deck_id=child.id, export_format=ExportFormat.CSV
    )

    assert exported.filename == "Verbs-irregular.csv"
    assert exported.media_type == "text/csv; charset=utf-8"
    rows = list(csv.reader(io.StringIO(exported.data.decode())))
    assert {row[0] for row in rows[1:]} == {"Verbs:irregular"}


async def test_export_over_the_card_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World()
    root_id = await _library(world)
    monkeypatch.setattr("flashcards.application.deck_export_service.MAX_EXPORT_CARDS", 2)
    with pytest.raises(ExportTooLarge):
        await world.export(root_id, ExportFormat.APKG)


def test_export_of_nothing_still_writes_a_collection() -> None:
    empty = DeckExport(decks=(), notes=(), exported_at=START)
    package = ApkgReader().read(FileDeckExportWriter().write(empty, ExportFormat.APKG))
    assert package.notes == () and set(package.decks) == {1}


def test_reviews_at_the_same_instant_get_distinct_revlog_ids() -> None:
    note = Note(
        id="n1",
        owner_id=ALICE,
        deck_id="d1",
        note_type=NoteType.BASIC,
        fields=(("front", "Q"), ("back", "A")),
        created_at=START,
    )
    card = Card(id="c1", owner_id=ALICE, deck_id="d1", note_id="n1", ordinal=0, created_at=START)
    logs = tuple(
        ReviewLog(id=f"l{i}", owner_id=ALICE, card_id="c1", rating=Rating.GOOD, reviewed_at=START)
        for i in range(3)
    )
    export = DeckExport(
        decks=(("Deck",),),
        notes=(ExportedNote(note, ("Deck",), (ExportedCard(card, None, logs),)),),
        exported_at=START,
    )

    package = ApkgReader().read(FileDeckExportWriter().write(export, ExportFormat.APKG))

    assert sorted(r.reviewed_at_ms for r in package.reviews) == [
        int(START.timestamp() * 1000) + offset for offset in range(3)
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Spanish Verbs", "Spanish-Verbs.apkg"),
        ("../../etc/passwd", "etc-passwd.apkg"),
        ("日本語", "deck.apkg"),
        ("a" * 300, "a" * 80 + ".apkg"),
    ],
)
def test_export_filenames_are_safe(name: str, expected: str) -> None:
    assert export_filename(name, ExportFormat.APKG) == expected


def test_media_types() -> None:
    assert ExportFormat.APKG.media_type == "application/octet-stream"
    assert ExportFormat.JSON.media_type == "application/json"
    assert deck_export.MAX_EXPORT_CARDS == 200_000
