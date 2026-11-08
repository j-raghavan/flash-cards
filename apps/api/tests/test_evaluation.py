# SPDX-License-Identifier: AGPL-3.0-only
"""The AI-evaluation parser and service (in-memory, fake LLM)."""

from __future__ import annotations

import pytest

from flashcards.api.container import Container, build_container
from flashcards.domain.ai import (
    LlmError,
    StructuredRequest,
    StructuredResult,
    TokenUsage,
)
from flashcards.domain.evaluation import (
    AnswerEvaluation,
    InvalidEvaluation,
    Verdict,
    parse_evaluation,
)
from flashcards.domain.notes import CardNotFound, NoteType


def test_parse_evaluation_reads_a_well_formed_grade() -> None:
    result = parse_evaluation(
        {"verdict": "partial", "score": 0.5, "explanation": "  Close, but missing why.  "}
    )
    assert result == AnswerEvaluation(
        verdict=Verdict.PARTIAL, score=0.5, explanation="Close, but missing why."
    )


def test_parse_evaluation_clamps_the_score() -> None:
    assert parse_evaluation({"verdict": "correct", "score": 4, "explanation": "x"}).score == 1.0
    assert parse_evaluation({"verdict": "incorrect", "score": -2, "explanation": "x"}).score == 0.0


@pytest.mark.parametrize(
    "raw",
    [
        {"score": 1, "explanation": "x"},  # no verdict
        {"verdict": 3, "score": 1, "explanation": "x"},  # verdict not a string
        {"verdict": "maybe", "score": 1, "explanation": "x"},  # unknown verdict
        {"verdict": "correct", "explanation": "x"},  # no score
        {"verdict": "correct", "score": "high", "explanation": "x"},  # score not a number
        {"verdict": "correct", "score": True, "explanation": "x"},  # bool is not a score
        {"verdict": "correct", "score": 1},  # no explanation
        {"verdict": "correct", "score": 1, "explanation": 9},  # explanation not a string
    ],
)
def test_parse_evaluation_rejects_malformed_grades(raw: dict[str, object]) -> None:
    with pytest.raises(InvalidEvaluation):
        parse_evaluation(raw)


class _FakeLLM:
    def __init__(self, data: dict[str, object] | None = None, error: Exception | None = None):
        self._data = data or {"verdict": "correct", "score": 1, "explanation": "Spot on."}
        self._error = error
        self.last_request: StructuredRequest | None = None

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        self.last_request = request
        if self._error is not None:
            raise self._error
        return StructuredResult(data=self._data, usage=TokenUsage(1, 1), model="fake")


async def _seed_card(container: Container, owner: str = "alice") -> str:
    deck = await container.decks.create(owner_id=owner, name="Bio")
    _note, cards = await container.notes.create_note(
        owner_id=owner,
        deck_id=deck.id,
        note_type=NoteType.BASIC,
        fields={"front": "Capital of France?", "back": "Paris"},
    )
    card_id: str = cards[0].id
    return card_id


async def test_evaluate_grades_the_answer_and_isolates_it_as_data() -> None:
    llm = _FakeLLM({"verdict": "correct", "score": 0.9, "explanation": "Right."})
    container = build_container(llm=llm)
    card_id = await _seed_card(container)

    # A distinct wrong guess, so it cannot be confused with the reference answer.
    result = await container.evaluation.evaluate(
        owner_id="alice", card_id=card_id, answer="Lyon-is-my-guess"
    )

    assert result.verdict is Verdict.CORRECT and result.score == 0.9
    request = llm.last_request
    assert request is not None
    # NF-SEC-6: the card's content and the student's answer are separate data
    # documents; neither is in the instructions.
    assert request.documents[0].data == b"front: Capital of France?\nback: Paris"
    assert request.documents[1].data == b"Lyon-is-my-guess"
    for text in ("Lyon-is-my-guess", "Capital of France?", "Paris"):
        assert text not in request.instructions


async def test_evaluate_is_owner_scoped() -> None:
    container = build_container(llm=_FakeLLM())
    card_id = await _seed_card(container, owner="alice")
    with pytest.raises(CardNotFound):
        await container.evaluation.evaluate(owner_id="bob", card_id=card_id, answer="Paris")


async def test_evaluate_propagates_a_malformed_grade() -> None:
    container = build_container(llm=_FakeLLM({"verdict": "nonsense"}))
    card_id = await _seed_card(container)
    with pytest.raises(InvalidEvaluation):
        await container.evaluation.evaluate(owner_id="alice", card_id=card_id, answer="Paris")


async def test_evaluate_propagates_a_provider_error() -> None:
    container = build_container(llm=_FakeLLM(error=LlmError("down")))
    card_id = await _seed_card(container)
    with pytest.raises(LlmError):
        await container.evaluation.evaluate(owner_id="alice", card_id=card_id, answer="Paris")
