# SPDX-License-Identifier: AGPL-3.0-only
"""The card browser's vocabulary: card state, proficiency, filters and rows (F-DECK-2).

Pure functions over a card, its note and its schedule, so both storage modes
filter and sort the same way (ADR 0011).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

from flashcards.domain.notes import Card, Note, NoteType
from flashcards.domain.scheduling import CardSchedule, ScheduleState, retrievability
from flashcards.domain.tags import Tags, has_tag

MAX_PAGE = 200
MAX_OFFSET = 100_000
MAX_QUERY_LENGTH = 200
PREVIEW_CHARS = 120
_SECONDS_PER_DAY = 86_400


class CardState(enum.StrEnum):
    NEW = "new"
    LEARNING = "learning"
    REVIEW = "review"
    SUSPENDED = "suspended"
    BURIED = "buried"


class SortKey(enum.StrEnum):
    CREATED = "created"
    DUE = "due"
    PROFICIENCY = "proficiency"
    DECK = "deck"


def card_state(card: Card, schedule: CardSchedule | None, now: datetime) -> CardState:
    """Suspended wins, then buried, then the schedule (none means new)."""
    if card.is_suspended:
        return CardState.SUSPENDED
    if card.is_buried(now):
        return CardState.BURIED
    if schedule is None:
        return CardState.NEW
    if schedule.state in (ScheduleState.LEARNING, ScheduleState.RELEARNING):
        return CardState.LEARNING
    return CardState.REVIEW


def card_proficiency(schedule: CardSchedule | None, now: datetime) -> int:
    """Percent chance of recalling the card now; 0 for a card never reviewed."""
    if (
        schedule is None
        or schedule.stability is None
        or schedule.stability <= 0  # never written by FSRS; tolerate bad data
        or schedule.last_reviewed_at is None
    ):
        return 0
    elapsed = (now - schedule.last_reviewed_at).total_seconds() / _SECONDS_PER_DAY
    return round(100 * retrievability(schedule.stability, elapsed))


def preview(note: Note) -> str:
    """The first field's text, trimmed for a table cell."""
    text = " ".join(note.fields[0][1].split()) if note.fields else ""
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "…"


@dataclass(frozen=True, slots=True)
class BrowserQuery:
    deck_ids: frozenset[str] | None = None  # None means every deck the owner has
    states: frozenset[CardState] = frozenset()  # empty means any state
    tag: str | None = None
    flag: int | None = None
    min_proficiency: int = 0
    max_proficiency: int = 100
    text: str | None = None
    sort: SortKey = SortKey.CREATED
    descending: bool = False
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class BrowserRow:
    card_id: str
    note_id: str
    deck_id: str
    deck_name: str
    ordinal: int
    note_type: NoteType
    preview: str
    tags: Tags
    flag: int
    state: CardState
    due_at: datetime | None  # None for a new card, which is due now
    proficiency: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BrowserPage:
    rows: tuple[BrowserRow, ...]
    total: int  # matches before paging


def matches(row: BrowserRow, note: Note, query: BrowserQuery) -> bool:
    if query.deck_ids is not None and row.deck_id not in query.deck_ids:
        return False
    if query.states and row.state not in query.states:
        return False
    if query.tag is not None and not has_tag(note.tags, query.tag):
        return False
    if query.flag is not None and row.flag != query.flag:
        return False
    if not query.min_proficiency <= row.proficiency <= query.max_proficiency:
        return False
    if query.text:
        needle = query.text.casefold()
        if not any(needle in value.casefold() for _name, value in note.fields):
            return False
    return True


def sort_key(row: BrowserRow, key: SortKey) -> tuple[object, ...]:
    """The value to sort on, then creation and id so equal values page stably."""
    primary: object
    if key is SortKey.DUE:
        primary = row.due_at or row.created_at  # a new card is due from creation
    elif key is SortKey.PROFICIENCY:
        primary = row.proficiency
    elif key is SortKey.DECK:
        primary = row.deck_name.casefold()
    else:
        primary = row.created_at
    return (primary, row.created_at, row.ordinal, row.card_id)
