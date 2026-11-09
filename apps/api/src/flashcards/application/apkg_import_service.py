# SPDX-License-Identifier: AGPL-3.0-only
"""Importing a parsed Anki package into decks and cards (F-IMP-1..3, ADR 0015).

The import is planned first, without writing anything: each note is selected or
not (by deck or tag), mapped to Basic or Cloze and validated, and its Anki cards
are matched to the cards our note will have. A dry run returns that plan as a
report. A real run then creates the decks (nested as in Anki), the notes, and for
each card its suspension, flag, review history and schedule. Notes whose type does
not map, or whose fields do not pass our validation, are counted as skipped rather
than failing the whole import, so a mostly-importable deck still imports.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime

from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.domain.anki import (
    DECK_SEPARATOR,
    AnkiCard,
    AnkiNote,
    AnkiNoteType,
    AnkiPackage,
    AnkiReview,
    ImportReport,
    ImportSelection,
    UnsupportedConstruct,
    UnsupportedKind,
    UnsupportedNote,
    deck_path,
    has_dropped_fields,
    in_selected_decks,
    map_note,
    our_ordinal,
)
from flashcards.domain.anki_schedule import (
    QUEUE_SUSPENDED,
    REVLOG_MANUAL,
    TYPE_NEW,
    rating_for,
    review_time,
    schedule_from_sm2,
)
from flashcards.domain.decks import MAX_DECK_NAME
from flashcards.domain.notes import (
    MAX_FLAG,
    Card,
    Fields,
    InvalidNote,
    NoteType,
    card_ordinals,
)
from flashcards.domain.ports import (
    CardRepository,
    Clock,
    PackageReader,
    ReviewLogRepository,
    Scheduler,
    ScheduleRepository,
)
from flashcards.domain.scheduling import CardSchedule, Rating, ReviewLog
from flashcards.domain.tags import Tags, has_tag, sanitize_tags

_FALLBACK_DECK_NAME = "Imported"
MAX_DECK_DEPTH = 8  # deeper Anki levels are kept in the name of the eighth
_MAX_EXAMPLES = 3
_MAX_EXAMPLE_LENGTH = 80
# Our cards table stores ordinals as 32-bit integers, and no real note has anywhere
# near this many cloze deletions; a larger number marks the note invalid.
MAX_CLOZE_ORDINAL = 1_000

History = tuple[tuple[Rating, datetime], ...]


@dataclass(frozen=True, slots=True)
class _CardHistory:
    answers: History  # every valid answer, oldest first: imported as review logs
    replay_from: int = 0  # answers before a manual reset (Forget) do not shape the schedule
    skipped: int = 0  # revlog rows that are not answers, or have an implausible time

    @property
    def replayed(self) -> History:
        return self.answers[self.replay_from :]


_NO_HISTORY = _CardHistory(answers=())


@dataclass(frozen=True, slots=True)
class _PlannedCard:
    anki: AnkiCard
    history: _CardHistory


@dataclass(frozen=True, slots=True)
class _PlannedNote:
    deck: tuple[str, ...]  # our cleaned deck path
    note_type: NoteType
    fields: Fields
    tags: Tags
    cards: dict[int, _PlannedCard]  # by our card ordinal


@dataclass(slots=True)
class _Plan:
    notes: list[_PlannedNote] = field(default_factory=list)
    skipped: int = 0
    not_selected: int = 0
    scheduled: int = 0
    reviews: int = 0
    counts: Counter[UnsupportedKind] = field(default_factory=Counter)
    examples: dict[UnsupportedKind, list[str]] = field(default_factory=dict)

    def flag(self, kind: UnsupportedKind, example: str = "", count: int = 1) -> None:
        self.counts[kind] += count
        shown = self.examples.setdefault(kind, [])
        cleaned = example.strip()[:_MAX_EXAMPLE_LENGTH]
        if cleaned and cleaned not in shown and len(shown) < _MAX_EXAMPLES:
            shown.append(cleaned)

    def unsupported(self) -> tuple[UnsupportedConstruct, ...]:
        return tuple(
            UnsupportedConstruct(kind, self.counts[kind], tuple(self.examples.get(kind, [])))
            for kind in UnsupportedKind
            if self.counts[kind]
        )


class ApkgImportService:
    def __init__(
        self,
        reader: PackageReader,
        decks: DeckService,
        notes: NoteService,
        cards: CardRepository,
        schedules: ScheduleRepository,
        logs: ReviewLogRepository,
        scheduler: Scheduler,
        clock: Clock,
    ) -> None:
        self._reader = reader
        self._decks = decks
        self._notes = notes
        self._cards = cards
        self._schedules = schedules
        self._logs = logs
        self._scheduler = scheduler
        self._clock = clock

    async def import_package(
        self,
        *,
        owner_id: str,
        data: bytes,
        selection: ImportSelection | None = None,
        dry_run: bool = False,
    ) -> ImportReport:
        # Parsing, planning and replaying histories are CPU work on untrusted input;
        # they run in a worker thread so one large import never stalls other requests.
        package = await asyncio.to_thread(self._reader.read, data)
        now = self._clock.now()
        plan = await asyncio.to_thread(_plan, package, selection or ImportSelection(), now)
        if dry_run:
            return _report(plan, decks_created=len(_deck_paths(plan)), dry_run=True)

        schedules = await asyncio.to_thread(
            _schedules_for, self._scheduler, plan, package.created_at_s, now
        )
        deck_ids = await self._create_decks(owner_id, _deck_paths(plan))
        for planned, planned_schedules in zip(plan.notes, schedules, strict=True):
            _note, cards = await self._notes.create_note(
                owner_id=owner_id,
                deck_id=deck_ids[planned.deck],
                note_type=planned.note_type,
                fields=dict(planned.fields),
                tags=planned.tags,
            )
            for card in cards:
                matched = planned.cards.get(card.ordinal)
                if matched is not None:
                    schedule = planned_schedules.get(card.ordinal)
                    await self._restore_card(owner_id, card, matched, schedule, now)
        return _report(plan, decks_created=len(deck_ids), dry_run=False)

    async def _create_decks(
        self, owner_id: str, paths: list[tuple[str, ...]]
    ) -> dict[tuple[str, ...], str]:
        """Create each deck path, parents first; return our deck id by path."""
        deck_ids: dict[tuple[str, ...], str] = {}
        for path in paths:
            parent_id = deck_ids.get(path[:-1])
            deck = await self._decks.create(owner_id=owner_id, name=path[-1], parent_id=parent_id)
            deck_ids[path] = deck.id
        return deck_ids

    async def _restore_card(
        self,
        owner_id: str,
        card: Card,
        matched: _PlannedCard,
        schedule: CardSchedule | None,
        now: datetime,
    ) -> None:
        flag = matched.anki.flag if 0 <= matched.anki.flag <= MAX_FLAG else 0
        restored = card.with_flag(flag)
        if matched.anki.queue == QUEUE_SUSPENDED:
            restored = restored.suspended(now)
        if restored != card:
            await self._cards.update(restored)

        for rating, reviewed_at in matched.history.answers:
            await self._logs.append(
                ReviewLog(
                    id=str(uuid.uuid4()),
                    owner_id=owner_id,
                    card_id=card.id,
                    rating=rating,
                    reviewed_at=reviewed_at,
                )
            )
        if schedule is not None:
            await self._schedules.upsert(replace(schedule, card_id=card.id))


def _schedules_for(
    scheduler: Scheduler, plan: _Plan, created_at_s: int | None, now: datetime
) -> list[dict[int, CardSchedule]]:
    """Each planned note's schedules by our ordinal (card ids are filled in later)."""
    result: list[dict[int, CardSchedule]] = []
    for planned in plan.notes:
        schedules: dict[int, CardSchedule] = {}
        for ordinal, matched in planned.cards.items():
            schedule = _schedule(scheduler, matched, created_at_s, now)
            if schedule is not None:
                schedules[ordinal] = schedule
        result.append(schedules)
    return result


def _schedule(
    scheduler: Scheduler, matched: _PlannedCard, created_at_s: int | None, now: datetime
) -> CardSchedule | None:
    """Replay the history through our scheduler, or fall back to the SM-2 columns."""
    if matched.anki.card_type == TYPE_NEW:
        return None  # new (or reset to new) in Anki: it starts over here too
    replayed = matched.history.replayed
    if not replayed:
        return schedule_from_sm2("", matched.anki, created_at_s, now)
    schedule = scheduler.new_schedule("", replayed[0][1])
    for rating, reviewed_at in replayed:
        schedule = scheduler.review(schedule, rating, reviewed_at)
    return schedule


def _plan(package: AnkiPackage, selection: ImportSelection, now: datetime) -> _Plan:
    plan = _Plan()
    cards_by_guid: dict[str, list[AnkiCard]] = {}
    for card in package.cards:
        cards_by_guid.setdefault(card.note_guid, []).append(card)
    history = _history_by_card(package, now)
    seen: set[str] = set()

    for note in package.notes:
        if note.guid in seen:  # a hostile file could give two notes the same cards
            plan.skipped += 1
            plan.flag(UnsupportedKind.DUPLICATE_NOTE)
            continue
        seen.add(note.guid)
        tags = sanitize_tags(note.tags)
        if selection.tags and not any(has_tag(tags, wanted) for wanted in selection.tags):
            plan.not_selected += 1
            continue
        anki_cards = cards_by_guid.get(note.guid)
        if not anki_cards:
            if selection.decks:
                plan.not_selected += 1  # in no deck, so in none of the selected ones
            else:
                plan.flag(UnsupportedKind.NOTE_WITHOUT_CARDS)
            continue  # a note with no card has nowhere to live
        deck_name, borrowed_from = _placement_deck(package, anki_cards[0])
        if not in_selected_decks(deck_name, selection.decks):
            plan.not_selected += 1
            continue
        if borrowed_from is not None:
            plan.flag(UnsupportedKind.FILTERED_DECK, borrowed_from)
        note_type = package.note_types.get(note.note_type_id)
        if note_type is None:
            plan.skipped += 1
            plan.flag(UnsupportedKind.UNKNOWN_NOTE_TYPE)
            continue
        planned = _plan_note(note, note_type, tags, deck_name, anki_cards, history, plan, now)
        if planned is not None:
            plan.notes.append(planned)

    if package.history_truncated:
        plan.flag(UnsupportedKind.HISTORY_TRUNCATED)
    if package.media_count:
        plan.flag(UnsupportedKind.MEDIA, count=package.media_count)
    return plan


def _plan_note(
    note: AnkiNote,
    note_type: AnkiNoteType,
    tags: Tags,
    deck_name: str,
    anki_cards: list[AnkiCard],
    history: dict[int, _CardHistory],
    plan: _Plan,
    now: datetime,
) -> _PlannedNote | None:
    try:
        our_type, fields = map_note(note_type, note.fields)
    except UnsupportedNote:
        plan.skipped += 1
        plan.flag(UnsupportedKind.UNMAPPABLE_NOTE, note_type.name)
        return None
    try:
        ordinals = set(card_ordinals(our_type, fields))
        if max(ordinals) > MAX_CLOZE_ORDINAL:
            raise InvalidNote("too many cloze deletions")
    except (InvalidNote, ValueError):  # ValueError: a cloze number too long to parse
        plan.skipped += 1
        plan.flag(UnsupportedKind.INVALID_NOTE, note_type.name)
        return None
    if has_dropped_fields(note_type, note.fields):
        plan.flag(UnsupportedKind.DROPPED_FIELDS, note_type.name)

    matched: dict[int, _PlannedCard] = {}
    extra = 0
    for anki_card in anki_cards:
        ordinal = our_ordinal(note_type, anki_card.ordinal)
        card_history = history.get(anki_card.id, _NO_HISTORY)
        if ordinal is None or ordinal not in ordinals or ordinal in matched:
            extra += 1
            dropped = len(card_history.answers) + card_history.skipped
            plan.flag(UnsupportedKind.SKIPPED_REVIEWS, count=dropped)
            continue
        matched[ordinal] = _PlannedCard(anki_card, card_history)
        plan.reviews += len(card_history.answers)
        plan.flag(UnsupportedKind.SKIPPED_REVIEWS, count=card_history.skipped)
        plan.scheduled += _will_be_scheduled(anki_card, card_history, now)
    if extra:
        plan.flag(UnsupportedKind.EXTRA_CARDS, note_type.name, count=extra)
    return _PlannedNote(
        deck=_clean_path(deck_name),
        note_type=our_type,
        fields=fields,
        tags=tags,
        cards=matched,
    )


def _will_be_scheduled(card: AnkiCard, history: _CardHistory, now: datetime) -> bool:
    # Mirrors _schedule without running the scheduler: a replay always yields a
    # schedule, and the SM-2 fallback is pure and cheap.
    if card.card_type == TYPE_NEW:
        return False
    return bool(history.replayed) or schedule_from_sm2("", card, None, now) is not None


def _history_by_card(package: AnkiPackage, now: datetime) -> dict[int, _CardHistory]:
    """Each card's answers, oldest first, and where its last manual reset falls.

    A manual revlog row (Forget, or an old-style set due date) is a reset: the
    answers before it stay in the history but are not replayed, so a relearned
    card never looks better known than its latest learning shows.
    """
    rows: dict[int, list[AnkiReview]] = {}
    for review in package.reviews:
        rows.setdefault(review.card_id, []).append(review)
    result: dict[int, _CardHistory] = {}
    for card_id, card_rows in rows.items():
        answers: list[tuple[Rating, datetime]] = []
        replay_from = skipped = 0
        for review in sorted(card_rows, key=lambda row: row.reviewed_at_ms):
            rating = rating_for(review)
            reviewed_at = review_time(review, now)
            if rating is None or reviewed_at is None:
                skipped += 1
                if review.kind == REVLOG_MANUAL:
                    replay_from = len(answers)
                continue
            answers.append((rating, reviewed_at))
        result[card_id] = _CardHistory(tuple(answers), replay_from, skipped)
    return result


def _placement_deck(package: AnkiPackage, card: AnkiCard) -> tuple[str, str | None]:
    """The name of the deck a note lands in (its first card's home deck), and the
    filtered deck it was borrowed by, if any.

    A card borrowed by a filtered deck goes back to the deck it came from.
    """
    deck = package.decks.get(card.deck_id)
    borrowed_from: str | None = None
    if card.original_deck_id or (deck is not None and deck.is_filtered):
        borrowed_from = deck.name if deck is not None else ""
        deck = package.decks.get(card.original_deck_id)
    return (deck.name if deck is not None else ""), borrowed_from


def _clean_path(name: str) -> tuple[str, ...]:
    # Bounded depth, so a hostile "a::a::a::..." name cannot fan out into a deck per level.
    parts = deck_path(name)
    if len(parts) > MAX_DECK_DEPTH:
        parts = (*parts[: MAX_DECK_DEPTH - 1], DECK_SEPARATOR.join(parts[MAX_DECK_DEPTH - 1 :]))
    return tuple(_clean_deck_name(part) for part in parts)


def _clean_deck_name(name: str) -> str:
    cleaned = name.strip()[:MAX_DECK_NAME].strip()
    return cleaned or _FALLBACK_DECK_NAME


def _deck_paths(plan: _Plan) -> list[tuple[str, ...]]:
    """Every deck the planned notes need, with all their parents, parents first."""
    paths: set[tuple[str, ...]] = set()
    for planned in plan.notes:
        for depth in range(1, len(planned.deck) + 1):
            paths.add(planned.deck[:depth])
    return sorted(paths, key=lambda path: (len(path), path))


def _report(plan: _Plan, *, decks_created: int, dry_run: bool) -> ImportReport:
    return ImportReport(
        decks_created=decks_created,
        notes_imported=len(plan.notes),
        notes_skipped=plan.skipped,
        notes_not_selected=plan.not_selected,
        cards_scheduled=plan.scheduled,
        reviews_imported=plan.reviews,
        dry_run=dry_run,
        unsupported=plan.unsupported(),
    )
