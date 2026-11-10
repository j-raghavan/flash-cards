# SPDX-License-Identifier: AGPL-3.0-only
"""Tags, flags and bury on notes and cards (F-CARD-6, ADR 0011)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryNoteRepository,
    InMemoryScheduleRepository,
)
from flashcards.domain.notes import (
    DEFAULT_BURY,
    MAX_BURY,
    Card,
    InvalidBury,
    InvalidFlag,
    Note,
    NoteType,
)
from flashcards.domain.scheduling import CardSchedule, ScheduleState
from flashcards.domain.tags import (
    MAX_TAG_LENGTH,
    MAX_TAGS_PER_NOTE,
    InvalidTags,
    has_tag,
    merge_tags,
    normalize_tags,
    remove_tags,
    sanitize_tags,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def test_tags_are_trimmed_deduplicated_ignoring_case_and_sorted() -> None:
    assert normalize_tags([" b ", "A", "a", "biology::cells", "B"]) == ("A", "b", "biology::cells")
    assert normalize_tags([]) == ()


@pytest.mark.parametrize("bad", ["", "   ", "two words", "tab\there", "x" * (MAX_TAG_LENGTH + 1)])
def test_invalid_tags_are_refused(bad: str) -> None:
    with pytest.raises(InvalidTags):
        normalize_tags([bad])


def test_a_note_has_a_tag_limit() -> None:
    with pytest.raises(InvalidTags, match="at most"):
        normalize_tags([f"t{i}" for i in range(MAX_TAGS_PER_NOTE + 1)])


def test_imported_tags_drop_what_is_invalid_instead_of_failing() -> None:
    raw = ["ok", "", "x" * 100, *[f"t{i:02}" for i in range(40)]]
    tags = sanitize_tags(raw)
    assert len(tags) == MAX_TAGS_PER_NOTE and "ok" in tags


def test_tag_matching_includes_descendants_only() -> None:
    tags = ("Biology::Cells", "chem")
    assert has_tag(tags, "biology") and has_tag(tags, "biology::cells") and has_tag(tags, "CHEM")
    assert not has_tag(tags, "bio") and not has_tag(tags, "biology::cells::x")


def test_adding_and_removing_tags() -> None:
    assert merge_tags(("a",), ["C", "A", "b"]) == ("a", "b", "C")
    assert remove_tags(("a", "B", "c"), ["b", " A "]) == ("c",)


def _card() -> Card:
    return Card(id="c", owner_id="o", deck_id="d", note_id="n", ordinal=0, created_at=NOW)


def test_bury_defaults_to_a_day_and_is_bounded() -> None:
    card = _card().buried(NOW)
    assert card.buried_until == NOW + DEFAULT_BURY
    assert card.is_buried(NOW) and not card.is_buried(NOW + DEFAULT_BURY)
    assert not card.unburied().is_buried(NOW)
    assert _card().buried(NOW, NOW + MAX_BURY).buried_until == NOW + MAX_BURY
    for until in (NOW, NOW - timedelta(hours=1), NOW + MAX_BURY + timedelta(seconds=1)):
        with pytest.raises(InvalidBury):
            _card().buried(NOW, until)


def test_flags_run_from_zero_to_seven() -> None:
    assert _card().with_flag(7).flag == 7 and _card().with_flag(3).with_flag(0).flag == 0
    for bad in (-1, 8):
        with pytest.raises(InvalidFlag):
            _card().with_flag(bad)


def test_moving_and_editing_return_changed_copies() -> None:
    note = Note(
        id="n", owner_id="o", deck_id="d", note_type=NoteType.BASIC,
        fields=(("front", "q"), ("back", "a")), created_at=NOW,
    )
    edited = note.with_fields((("front", "Q"), ("back", "A"))).with_tags(("t",)).moved_to("e")
    assert (edited.field("front"), edited.tags, edited.deck_id) == ("Q", ("t",), "e")
    assert _card().moved_to("e").deck_id == "e"


async def test_owner_listings_updates_and_batched_schedules_in_memory() -> None:
    notes, cards, schedules = (
        InMemoryNoteRepository(), InMemoryCardRepository(), InMemoryScheduleRepository()
    )
    for owner, nid in (("o", "n1"), ("o", "n2"), ("x", "n3")):
        await notes.add(
            Note(id=nid, owner_id=owner, deck_id="d", note_type=NoteType.BASIC,
                 fields=(("front", "q"), ("back", "a")), created_at=NOW)
        )
        await cards.add(
            Card(id=f"c-{nid}", owner_id=owner, deck_id="d", note_id=nid, ordinal=0,
                 created_at=NOW)
        )
    assert [n.id for n in await notes.list_for_owner("o")] == ["n1", "n2"]
    assert [c.id for c in await cards.list_for_owner("o")] == ["c-n1", "c-n2"]

    first = await notes.get("n1")
    assert first is not None
    await notes.update(first.with_tags(("t",)))
    await notes.update(
        Note(id="missing", owner_id="o", deck_id="d", note_type=NoteType.BASIC, fields=(),
             created_at=NOW)
    )  # updating a missing note is a no-op
    stored = await notes.get("n1")
    assert stored is not None and stored.tags == ("t",)
    assert await notes.get("missing") is None

    await cards.delete("c-n2")
    assert [c.id for c in await cards.list_for_owner("o")] == ["c-n1"]

    schedule = CardSchedule(
        card_id="c-n1", state=ScheduleState.REVIEW, step=None, stability=3.0, difficulty=5.0,
        due_at=NOW, last_reviewed_at=NOW,
    )
    await schedules.upsert("o", schedule)
    assert await schedules.get_many("o", ["c-n1", "c-n3"]) == {"c-n1": schedule}
