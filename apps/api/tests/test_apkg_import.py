# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime

from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryNoteRepository,
)
from flashcards.application.apkg_import_service import ApkgImportService
from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.domain.anki import (
    AnkiCard,
    AnkiDeck,
    AnkiNote,
    AnkiNoteType,
    AnkiPackage,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ALICE = "alice"

BASIC = AnkiNoteType(id=1, name="Basic", is_cloze=False, field_names=("Front", "Back"))
CLOZE = AnkiNoteType(id=2, name="Cloze", is_cloze=True, field_names=("Text",))
SINGLE = AnkiNoteType(id=3, name="Prompt", is_cloze=False, field_names=("Only",))


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakeReader:
    def __init__(self, package: AnkiPackage) -> None:
        self._package = package

    async def read(self, data: bytes) -> AnkiPackage:
        return self._package


class _Fixture:
    def __init__(self, package: AnkiPackage) -> None:
        clock = FakeClock()
        self.decks = DeckService(InMemoryDeckRepository(), clock)
        self.notes = NoteService(
            self.decks, InMemoryNoteRepository(), InMemoryCardRepository(), clock
        )
        self.service = ApkgImportService(FakeReader(package), self.decks, self.notes)


async def test_imports_decks_notes_and_skips_the_unmappable() -> None:
    package = AnkiPackage(
        note_types={1: BASIC, 2: CLOZE, 3: SINGLE},
        decks={100: AnkiDeck(100, "Biology"), 200: AnkiDeck(200, "Chemistry")},
        notes=(
            AnkiNote("g1", 1, ("Capital?", "Paris"), ("geo::europe", "Geo::Europe", "")),
            AnkiNote("g2", 2, ("The {{c1::cell}}",), ()),
            AnkiNote("g3", 3, ("only one field",), ()),  # too few fields -> skipped
            AnkiNote("g4", 1, ("Q4", "A4"), ()),
            AnkiNote("g5", 999, ("x", "y"), ()),  # unknown note type -> skipped
            AnkiNote("g6", 1, ("orphan", "no card"), ()),  # no card -> not placed
            AnkiNote("g7", 2, ("no cloze markers",), ()),  # invalid cloze -> skipped
        ),
        cards=(
            AnkiCard("g1", 100, 0),
            AnkiCard("g2", 100, 0),
            AnkiCard("g3", 100, 0),
            AnkiCard("g4", 200, 0),
            AnkiCard("g5", 100, 0),
            AnkiCard("g7", 100, 0),
        ),
    )
    fx = _Fixture(package)

    report = await fx.service.import_package(owner_id=ALICE, data=b"ignored")

    assert report.decks_created == 2
    assert report.notes_imported == 3  # g1, g2, g4
    assert report.notes_skipped == 3  # g3 (fields), g5 (type), g7 (invalid cloze)

    decks = {deck.name: deck.id for deck in await fx.decks.list_decks(owner_id=ALICE)}
    assert set(decks) == {"Biology", "Chemistry"}
    bio_cards = await fx.notes.list_cards(owner_id=ALICE, deck_id=decks["Biology"])
    chem_cards = await fx.notes.list_cards(owner_id=ALICE, deck_id=decks["Chemistry"])
    assert len(bio_cards) == 2  # g1 (1) + g2 cloze with one deletion (1)
    assert len(chem_cards) == 1  # g4
    # Tags come across, cleaned up (duplicates and blanks dropped).
    g1 = await fx.notes.get_note(owner_id=ALICE, note_id=bio_cards[0].note_id)
    assert g1.tags == ("geo::europe",)


async def test_unknown_or_blank_deck_names_fall_back() -> None:
    package = AnkiPackage(
        note_types={1: BASIC},
        decks={100: AnkiDeck(100, "   ")},  # blank name, and deck 300 is absent
        notes=(AnkiNote("g1", 1, ("Q", "A"), ()), AnkiNote("g2", 1, ("Q2", "A2"), ())),
        cards=(AnkiCard("g1", 100, 0), AnkiCard("g2", 300, 0)),
    )
    fx = _Fixture(package)

    report = await fx.service.import_package(owner_id=ALICE, data=b"ignored")

    assert report.decks_created == 2 and report.notes_imported == 2
    names = {deck.name for deck in await fx.decks.list_decks(owner_id=ALICE)}
    assert names == {"Imported"}  # both the blank and the missing deck fall back


async def test_empty_package_imports_nothing() -> None:
    package = AnkiPackage(note_types={}, decks={}, notes=(), cards=())
    fx = _Fixture(package)
    report = await fx.service.import_package(owner_id=ALICE, data=b"ignored")
    assert (report.decks_created, report.notes_imported, report.notes_skipped) == (0, 0, 0)
