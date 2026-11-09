# SPDX-License-Identifier: AGPL-3.0-only
"""Importing a parsed Anki package (F-IMP-1..3): mapping, selection, dry run, history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flashcards.adapters.fsrs_scheduler import FsrsScheduler
from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryNoteRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
)
from flashcards.application import apkg_import_service
from flashcards.application.apkg_import_service import ApkgImportService
from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.domain.anki import (
    AnkiCard,
    AnkiDeck,
    AnkiNote,
    AnkiNoteType,
    AnkiPackage,
    AnkiReview,
    ImportReport,
    ImportSelection,
    UnsupportedConstruct,
    UnsupportedKind,
)
from flashcards.domain.anki_schedule import TYPE_LEARNING, TYPE_NEW, TYPE_REVIEW
from flashcards.domain.decks import Deck
from flashcards.domain.notes import Card
from flashcards.domain.scheduling import Rating, ScheduleState

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ALICE = "alice"
CRT = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())

BASIC = AnkiNoteType(id=1, name="Basic", is_cloze=False, field_names=("Front", "Back"))
CLOZE = AnkiNoteType(id=2, name="Cloze", is_cloze=True, field_names=("Text",))
SINGLE = AnkiNoteType(id=3, name="Prompt", is_cloze=False, field_names=("Only",))
REVERSED = AnkiNoteType(
    id=4, name="Basic (and reversed card)", is_cloze=False, field_names=("F", "B"), template_count=2
)
TYPES = {1: BASIC, 2: CLOZE, 3: SINGLE, 4: REVERSED}


def _ms(days_ago: float) -> int:
    return int((NOW - timedelta(days=days_ago)).timestamp() * 1000)


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
        self.deck_repo = InMemoryDeckRepository()
        self.card_repo = InMemoryCardRepository()
        self.schedules = InMemoryScheduleRepository()
        self.logs = InMemoryReviewLogRepository()
        self.decks = DeckService(self.deck_repo, clock)
        self.notes = NoteService(self.decks, InMemoryNoteRepository(), self.card_repo, clock)
        self.scheduler = FsrsScheduler()
        self.service = ApkgImportService(
            FakeReader(package),
            self.decks,
            self.notes,
            self.card_repo,
            self.schedules,
            self.logs,
            self.scheduler,
            clock,
        )

    async def run(
        self, selection: ImportSelection | None = None, *, dry_run: bool = False
    ) -> ImportReport:
        return await self.service.import_package(
            owner_id=ALICE, data=b"ignored", selection=selection, dry_run=dry_run
        )

    async def deck_named(self, name: str) -> Deck:
        return next(d for d in await self.decks.list_decks(ALICE) if d.name == name)

    async def cards_in(self, name: str) -> list[Card]:
        deck = await self.deck_named(name)
        return await self.notes.list_cards(owner_id=ALICE, deck_id=deck.id)


def _kinds(report: ImportReport) -> dict[UnsupportedKind, int]:
    return {item.kind: item.count for item in report.unsupported}


async def test_imports_decks_notes_and_skips_the_unmappable() -> None:
    package = AnkiPackage(
        note_types=TYPES,
        decks={100: AnkiDeck(100, "Biology"), 200: AnkiDeck(200, "Chemistry")},
        notes=(
            AnkiNote("g1", 1, ("Capital?", "Paris", "atlas"), ("geo::europe", "Geo::Europe", "")),
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
        media_count=4,
    )
    fx = _Fixture(package)

    report = await fx.run()

    assert report.decks_created == 2
    assert report.notes_imported == 3  # g1, g2, g4
    assert report.notes_skipped == 3  # g3 (fields), g5 (type), g7 (invalid cloze)
    assert report.dry_run is False and report.cards_scheduled == 0
    assert report.unsupported == (
        UnsupportedConstruct(UnsupportedKind.UNKNOWN_NOTE_TYPE, 1),
        UnsupportedConstruct(UnsupportedKind.UNMAPPABLE_NOTE, 1, ("Prompt",)),
        UnsupportedConstruct(UnsupportedKind.INVALID_NOTE, 1, ("Cloze",)),
        UnsupportedConstruct(UnsupportedKind.DROPPED_FIELDS, 1, ("Basic",)),
        UnsupportedConstruct(UnsupportedKind.MEDIA, 4),
        UnsupportedConstruct(UnsupportedKind.NOTE_WITHOUT_CARDS, 1),
    )

    assert len(await fx.cards_in("Biology")) == 2  # g1 (1) + g2 cloze with one deletion (1)
    chem_cards = await fx.cards_in("Chemistry")
    assert len(chem_cards) == 1  # g4
    bio_cards = await fx.cards_in("Biology")
    g1 = await fx.notes.get_note(owner_id=ALICE, note_id=bio_cards[0].note_id)
    assert g1.tags == ("geo::europe",)  # duplicates and blanks dropped


async def test_unknown_or_blank_deck_names_fall_back_to_one_deck() -> None:
    package = AnkiPackage(
        note_types={1: BASIC},
        decks={100: AnkiDeck(100, "   ")},  # blank name, and deck 300 is absent
        notes=(AnkiNote("g1", 1, ("Q", "A"), ()), AnkiNote("g2", 1, ("Q2", "A2"), ())),
        cards=(AnkiCard("g1", 100, 0), AnkiCard("g2", 300, 0)),
    )
    fx = _Fixture(package)

    report = await fx.run()

    assert report.decks_created == 1 and report.notes_imported == 2
    assert [deck.name for deck in await fx.decks.list_decks(ALICE)] == ["Imported"]


async def test_empty_package_imports_nothing() -> None:
    fx = _Fixture(AnkiPackage(note_types={}, decks={}, notes=(), cards=()))
    report = await fx.run()
    assert (report.decks_created, report.notes_imported, report.notes_skipped) == (0, 0, 0)
    assert report.unsupported == ()


async def test_nested_anki_decks_become_nested_decks() -> None:
    package = AnkiPackage(
        note_types={1: BASIC},
        decks={
            1: AnkiDeck(1, "Languages::Spanish::Verbs"),
            2: AnkiDeck(2, "Languages"),
            3: AnkiDeck(3, "Languages::Spanish:: "),  # a blank level falls back
        },
        notes=(
            AnkiNote("g1", 1, ("ser", "to be"), ()),
            AnkiNote("g2", 1, ("hola", "hello"), ()),
            AnkiNote("g3", 1, ("x", "y"), ()),
        ),
        cards=(AnkiCard("g1", 1, 0), AnkiCard("g2", 2, 0), AnkiCard("g3", 3, 0)),
    )
    fx = _Fixture(package)

    report = await fx.run()

    assert report.decks_created == 4  # Languages, Spanish, Verbs, Imported
    by_name = {deck.name: deck for deck in await fx.decks.list_decks(ALICE)}
    assert by_name["Languages"].parent_id is None
    assert by_name["Spanish"].parent_id == by_name["Languages"].id
    assert by_name["Verbs"].parent_id == by_name["Spanish"].id
    assert by_name["Imported"].parent_id == by_name["Spanish"].id
    assert len(await fx.cards_in("Verbs")) == 1 and len(await fx.cards_in("Languages")) == 1


async def test_deep_deck_names_are_capped_in_depth() -> None:
    name = "::".join(f"L{level}" for level in range(12))
    package = AnkiPackage(
        note_types={1: BASIC},
        decks={1: AnkiDeck(1, name)},
        notes=(AnkiNote("g1", 1, ("Q", "A"), ()),),
        cards=(AnkiCard("g1", 1, 0),),
    )
    fx = _Fixture(package)

    report = await fx.run()

    assert report.decks_created == apkg_import_service.MAX_DECK_DEPTH
    assert len(await fx.cards_in("L7::L8::L9::L10::L11")) == 1


def _selection_package() -> AnkiPackage:
    return AnkiPackage(
        note_types={1: BASIC},
        decks={1: AnkiDeck(1, "Languages::Spanish"), 2: AnkiDeck(2, "Biology")},
        notes=(
            AnkiNote("g1", 1, ("ser", "to be"), ("verbs::irregular",)),
            AnkiNote("g2", 1, ("casa", "house"), ("nouns",)),
            AnkiNote("g3", 1, ("cell", "unit of life"), ("verbs",)),
            AnkiNote("g4", 1, ("no", "card"), ("verbs",)),
        ),
        cards=(AnkiCard("g1", 1, 0), AnkiCard("g2", 1, 0), AnkiCard("g3", 2, 0)),
    )


async def test_selection_by_sub_deck() -> None:
    fx = _Fixture(_selection_package())

    report = await fx.run(ImportSelection(decks=("LANGUAGES",)))

    assert (report.notes_imported, report.notes_not_selected) == (2, 2)  # g3, and card-less g4
    assert {deck.name for deck in await fx.decks.list_decks(ALICE)} == {"Languages", "Spanish"}
    assert report.unsupported == ()  # a card-less note cannot be in a selected deck


async def test_selection_by_tag_matches_descendant_tags() -> None:
    fx = _Fixture(_selection_package())

    report = await fx.run(ImportSelection(tags=("verbs",)))

    assert (report.notes_imported, report.notes_not_selected) == (2, 1)  # g1, g3; not g2
    assert _kinds(report) == {UnsupportedKind.NOTE_WITHOUT_CARDS: 1}  # g4


async def test_selection_by_deck_and_tag_together() -> None:
    fx = _Fixture(_selection_package())

    report = await fx.run(ImportSelection(decks=("Biology",), tags=("verbs",)))

    assert (report.notes_imported, report.notes_not_selected) == (1, 3)


def _history_package() -> AnkiPackage:
    return AnkiPackage(
        note_types=TYPES,
        decks={1: AnkiDeck(1, "Deck"), 9: AnkiDeck(9, "Cram", is_filtered=True)},
        notes=(
            AnkiNote("replayed", 1, ("Q1", "A1"), ()),
            AnkiNote("sm2", 1, ("Q2", "A2"), ()),
            AnkiNote("learning", 1, ("Q3", "A3"), ()),
            AnkiNote("reset", 1, ("Q4", "A4"), ()),
            AnkiNote("reversed", 4, ("Q5", "A5"), ()),
            AnkiNote("cloze", 2, ("{{c1::a}} {{c2::b}}",), ()),
        ),
        cards=(
            # In a filtered deck, borrowed from deck 1, suspended and flagged.
            AnkiCard("replayed", 9, 0, id=11, card_type=TYPE_REVIEW, queue=-1,
                     due=300, interval=4, factor=2500, original_deck_id=1, flag=3),
            AnkiCard("sm2", 1, 0, id=12, card_type=TYPE_REVIEW, queue=2,
                     due=320, interval=10, factor=2000, flag=9),
            AnkiCard("learning", 1, 0, id=13, card_type=TYPE_LEARNING, queue=1),
            AnkiCard("reset", 1, 0, id=14, card_type=TYPE_NEW),
            AnkiCard("reversed", 1, 0, id=15),
            AnkiCard("reversed", 1, 1, id=16, card_type=TYPE_REVIEW, interval=3),
            AnkiCard("cloze", 1, 1, id=17, card_type=TYPE_REVIEW, interval=5),  # c2
            AnkiCard("cloze", 1, 1, id=18),  # a second card for c2: no counterpart
            AnkiCard("cloze", 1, 4, id=19),  # c5 is not in the text
        ),
        reviews=(
            AnkiReview(11, _ms(10), 3, 0),
            AnkiReview(11, _ms(9), 1, 1),
            AnkiReview(11, _ms(9.5), 0, 4),  # manual (Forget): skipped, and a reset
            AnkiReview(11, _ms(5), 3, 2),
            AnkiReview(11, _ms(-1), 3, 1),  # in the future: skipped
            AnkiReview(14, _ms(30), 4, 0),  # history of a card reset to new
            AnkiReview(16, _ms(3), 3, 1),  # the reversed card has no counterpart
        ),
        created_at_s=CRT,
        history_truncated=True,
    )


async def test_history_is_replayed_and_state_restored() -> None:
    fx = _Fixture(_history_package())

    report = await fx.run()

    assert report.notes_imported == 6
    assert report.reviews_imported == 4  # three for `replayed`, one for `reset`
    assert report.cards_scheduled == 3  # replayed, sm2 and the cloze c2 card
    assert _kinds(report) == {
        UnsupportedKind.EXTRA_CARDS: 3,
        UnsupportedKind.FILTERED_DECK: 1,
        UnsupportedKind.SKIPPED_REVIEWS: 3,
        UnsupportedKind.HISTORY_TRUNCATED: 1,
    }
    cards = await fx.cards_in("Deck")
    first_field = {}
    for card in cards:
        note = await fx.notes.get_note(owner_id=ALICE, note_id=card.note_id)
        first_field[card.id] = note.field("front") or note.field("text")

    def card_of(front: str, ordinal: int = 0) -> Card:
        return next(c for c in cards if c.ordinal == ordinal and first_field[c.id] == front)

    replayed = card_of("Q1")
    assert replayed.is_suspended and replayed.flag == 3
    schedule = await fx.schedules.get(replayed.id)
    # Only the answers after the Forget shape the schedule; all of them are history.
    expected = fx.scheduler.new_schedule(replayed.id, NOW - timedelta(days=9))
    for rating, days_ago in ((Rating.AGAIN, 9), (Rating.GOOD, 5)):
        expected = fx.scheduler.review(expected, rating, NOW - timedelta(days=days_ago))
    assert schedule == expected
    logs = [log for log in await fx.logs.list_for_owner(ALICE) if log.card_id == replayed.id]
    assert [log.rating for log in logs] == [Rating.GOOD, Rating.AGAIN, Rating.GOOD]

    sm2_card = card_of("Q2")
    assert sm2_card.flag == 0 and not sm2_card.is_suspended  # an out-of-range flag is dropped
    sm2 = await fx.schedules.get(sm2_card.id)
    assert sm2 is not None and sm2.state is ScheduleState.REVIEW and sm2.stability == 10.0

    assert await fx.schedules.get(card_of("Q3").id) is None  # learning, no history: new
    reset = card_of("Q4")
    assert await fx.schedules.get(reset.id) is None  # new in Anki: starts over
    assert [log.card_id for log in await fx.logs.list_for_owner(ALICE)].count(reset.id) == 1
    assert await fx.schedules.get(card_of("{{c1::a}} {{c2::b}}", 2).id) is not None
    assert await fx.schedules.get(card_of("{{c1::a}} {{c2::b}}", 1).id) is None


async def test_dry_run_reports_the_same_plan_and_writes_nothing() -> None:
    dry = _Fixture(_history_package())
    real = _Fixture(_history_package())

    planned = await dry.run(dry_run=True)
    done = await real.run()

    assert planned.dry_run is True
    assert planned.decks_created == done.decks_created == 1
    assert (planned.notes_imported, planned.reviews_imported, planned.cards_scheduled) == (
        done.notes_imported,
        done.reviews_imported,
        done.cards_scheduled,
    )
    assert planned.unsupported == done.unsupported
    assert await dry.decks.list_decks(ALICE) == []
    assert await dry.card_repo.list_for_owner(ALICE) == []
    assert await dry.logs.list_for_owner(ALICE) == []


async def test_filtered_deck_without_an_original_deck_falls_back() -> None:
    package = AnkiPackage(
        note_types={1: BASIC},
        decks={9: AnkiDeck(9, "Cram", is_filtered=True)},
        notes=(AnkiNote("g1", 1, ("Q", "A"), ()), AnkiNote("g2", 1, ("Q2", "A2"), ())),
        cards=(AnkiCard("g1", 9, 0), AnkiCard("g2", 404, 0, original_deck_id=405)),
    )
    fx = _Fixture(package)

    report = await fx.run()

    assert [deck.name for deck in await fx.decks.list_decks(ALICE)] == ["Imported"]
    assert report.unsupported == (
        UnsupportedConstruct(UnsupportedKind.FILTERED_DECK, 2, ("Cram",)),
    )


async def test_a_card_reset_with_no_answers_since_uses_its_sm2_columns() -> None:
    package = AnkiPackage(
        note_types={1: BASIC},
        decks={1: AnkiDeck(1, "Deck")},
        notes=(AnkiNote("g1", 1, ("Q", "A"), ()),),
        cards=(AnkiCard("g1", 1, 0, id=1, card_type=TYPE_REVIEW, interval=6, due=300),),
        reviews=(AnkiReview(1, _ms(20), 3, 1), AnkiReview(1, _ms(10), 0, 4)),
        created_at_s=CRT,
    )
    fx = _Fixture(package)

    report = await fx.run()

    (card,) = await fx.cards_in("Deck")
    schedule = await fx.schedules.get(card.id)
    assert schedule is not None and schedule.stability == 6.0  # SM-2, not the old answer
    assert report.reviews_imported == 1 and report.cards_scheduled == 1


async def test_duplicate_guids_and_huge_cloze_numbers_are_skipped() -> None:
    package = AnkiPackage(
        note_types={1: BASIC, 2: CLOZE},
        decks={1: AnkiDeck(1, "Deck")},
        notes=(
            AnkiNote("g1", 1, ("Q", "A"), ()),
            AnkiNote("g1", 1, ("same guid", "again"), ()),
            AnkiNote("g2", 2, ("{{c1001::too many}}",), ()),
            AnkiNote("g3", 2, ("{{c" + "9" * 5000 + "::unparseable}}",), ()),
        ),
        cards=(
            AnkiCard("g1", 1, 0, id=1),
            AnkiCard("g2", 1, 0, id=2),
            AnkiCard("g3", 1, 0, id=3),
        ),
        reviews=(AnkiReview(1, _ms(2), 3, 0),),
    )
    for dry_run in (True, False):
        report = await _Fixture(package).run(dry_run=dry_run)
        assert (report.notes_imported, report.notes_skipped) == (1, 3)
        assert report.reviews_imported == 1  # not counted twice for the duplicate
        assert _kinds(report) == {
            UnsupportedKind.DUPLICATE_NOTE: 1,
            UnsupportedKind.INVALID_NOTE: 2,
        }


async def test_examples_are_unique_short_and_few() -> None:
    names = ["A", "A", "B", "C", "D", "x" * 200]
    types = {i: AnkiNoteType(i, name, False, ("Only",)) for i, name in enumerate(names)}
    package = AnkiPackage(
        note_types=types,
        decks={1: AnkiDeck(1, "Deck")},
        notes=tuple(AnkiNote(f"g{i}", i, ("one",), ()) for i in types),
        cards=tuple(AnkiCard(f"g{i}", 1, 0) for i in types),
    )
    fx = _Fixture(package)

    report = await fx.run()

    assert report.unsupported == (
        UnsupportedConstruct(UnsupportedKind.UNMAPPABLE_NOTE, 6, ("A", "B", "C")),
    )
