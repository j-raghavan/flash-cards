# SPDX-License-Identifier: AGPL-3.0-only
"""The deck-summary parser and service (in-memory, fake LLM)."""

from __future__ import annotations

import pytest

from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryNoteRepository,
    InMemoryQuotaRepository,
    SystemClock,
)
from flashcards.application.ai_budget import AiCallBudget
from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.application.summary_service import SummaryService
from flashcards.domain.ai import StructuredRequest, StructuredResult, TokenUsage
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.notes import NoteType
from flashcards.domain.summary import (
    DeckSummary,
    InvalidSummary,
    SummaryLocked,
    parse_summary,
)


def test_parse_summary_reads_text_and_points() -> None:
    result = parse_summary(
        {"summary": "  All about cells.  ", "key_points": [" mitochondria ", "", 7, "nucleus"]}
    )
    assert result == DeckSummary(
        summary="All about cells.", key_points=("mitochondria", "nucleus")
    )


@pytest.mark.parametrize(
    "raw",
    [
        {"key_points": ["a"]},  # no summary
        {"summary": "   ", "key_points": ["a"]},  # blank summary
        {"summary": 5, "key_points": ["a"]},  # summary not a string
        {"summary": "x"},  # no key_points
        {"summary": "x", "key_points": "not-a-list"},  # key_points not a list
    ],
)
def test_parse_summary_rejects_malformed(raw: dict[str, object]) -> None:
    with pytest.raises(InvalidSummary):
        parse_summary(raw)


class _FakeLLM:
    def __init__(self) -> None:
        self.last_request: StructuredRequest | None = None

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        self.last_request = request
        return StructuredResult(
            data={"summary": "A summary.", "key_points": ["one", "two"]},
            usage=TokenUsage(1, 1),
            model="fake",
        )


def _service(
    llm: _FakeLLM, *, unlock_cards: int
) -> tuple[SummaryService, DeckService, NoteService]:
    clock = SystemClock()
    decks_repo = InMemoryDeckRepository()
    notes_repo = InMemoryNoteRepository()
    cards_repo = InMemoryCardRepository()
    decks = DeckService(decks_repo, clock)
    notes = NoteService(decks, notes_repo, cards_repo, clock)
    budget = AiCallBudget(InMemoryQuotaRepository(limit=100), clock)
    service = SummaryService(
        decks, notes_repo, cards_repo, llm, budget, unlock_cards=unlock_cards
    )
    return service, decks, notes


async def _add_cards(notes: NoteService, decks: DeckService, owner: str, count: int) -> str:
    deck = await decks.create(owner_id=owner, name="Bio")
    for i in range(count):
        await notes.create_note(
            owner_id=owner,
            deck_id=deck.id,
            note_type=NoteType.BASIC,
            fields={"front": f"Q{i}", "back": f"A{i}"},
        )
    return deck.id


async def test_summary_is_locked_below_the_threshold() -> None:
    service, decks, notes = _service(_FakeLLM(), unlock_cards=3)
    deck_id = await _add_cards(notes, decks, "alice", 2)
    with pytest.raises(SummaryLocked) as caught:
        await service.summarize(owner_id="alice", deck_id=deck_id)
    assert caught.value.required == 3 and caught.value.have == 2


async def test_summary_unlocks_at_the_threshold() -> None:
    llm = _FakeLLM()
    service, decks, notes = _service(llm, unlock_cards=2)
    deck_id = await _add_cards(notes, decks, "alice", 2)

    result = await service.summarize(owner_id="alice", deck_id=deck_id)

    assert result.summary == "A summary." and result.key_points == ("one", "two")
    request = llm.last_request
    assert request is not None
    # The deck's material is a data document, not in the instructions.
    assert b"Q0" in request.documents[0].data and "Q0" not in request.instructions


async def test_summary_counts_subdeck_cards() -> None:
    llm = _FakeLLM()
    service, decks, notes = _service(llm, unlock_cards=2)
    parent = await decks.create(owner_id="alice", name="Parent")
    child = await _add_cards(notes, decks, "alice", 2)
    await decks.move(owner_id="alice", deck_id=child, new_parent_id=parent.id)

    result = await service.summarize(owner_id="alice", deck_id=parent.id)

    assert result.summary == "A summary."
    request = llm.last_request
    assert request is not None and b"Q1" in request.documents[0].data


async def test_summary_is_owner_scoped() -> None:
    service, decks, notes = _service(_FakeLLM(), unlock_cards=1)
    deck_id = await _add_cards(notes, decks, "alice", 1)
    with pytest.raises(DeckNotFound):
        await service.summarize(owner_id="bob", deck_id=deck_id)


async def test_summary_source_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "flashcards.application.summary_service.MAX_SUMMARY_SOURCE_CHARS", 5
    )
    llm = _FakeLLM()
    service, decks, notes = _service(llm, unlock_cards=1)
    deck_id = await _add_cards(notes, decks, "alice", 2)

    await service.summarize(owner_id="alice", deck_id=deck_id)

    request = llm.last_request
    assert request is not None
    # The cap is below even the first line, so the source is truncated to nothing.
    assert len(request.documents[0].data) <= 5
