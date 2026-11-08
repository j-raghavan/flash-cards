# SPDX-License-Identifier: AGPL-3.0-only
"""The mock-exam parsers, grader and service (in-memory, scripted LLM)."""

from __future__ import annotations

import pytest

from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryMockExamRepository,
    InMemoryNoteRepository,
    SystemClock,
)
from flashcards.application.deck_service import DeckService
from flashcards.application.mock_exam_service import MockExamService
from flashcards.application.note_service import NoteService
from flashcards.domain.ai import StructuredRequest, StructuredResult, TokenUsage
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.mock_exam import (
    ExamLocked,
    ExamNotFound,
    ExamQuestion,
    InvalidExam,
    MockExam,
    QuestionScore,
    grade_exam,
    parse_exam_questions,
    parse_grades,
)
from flashcards.domain.notes import NoteType

NOW_QUESTIONS: dict[str, object] = {
    "questions": [
        {"prompt": "What is a cell?", "answer": "The unit of life", "topic": "Biology"},
        {"prompt": "Define osmosis", "answer": "Water across a membrane", "topic": "Biology"},
    ]
}


def test_parse_exam_questions_reads_triples_and_defaults_topic() -> None:
    raw = {"questions": [{"prompt": " Q ", "answer": " A ", "topic": "  "}]}
    assert parse_exam_questions(raw, limit=5) == [("Q", "A", "General")]


def test_parse_exam_questions_truncates_to_the_limit() -> None:
    assert len(parse_exam_questions(NOW_QUESTIONS, limit=1)) == 1


@pytest.mark.parametrize(
    "raw",
    [
        {"questions": []},  # empty
        {"questions": "nope"},  # not a list
        {"questions": ["not-a-dict"]},  # item not an object
        {"questions": [{"answer": "A", "topic": "T"}]},  # no prompt
        {"questions": [{"prompt": "Q", "topic": "T"}]},  # no answer
        {"questions": [{"prompt": "  ", "answer": "A", "topic": "T"}]},  # blank prompt
    ],
)
def test_parse_exam_questions_rejects_malformed(raw: dict[str, object]) -> None:
    with pytest.raises(InvalidExam):
        parse_exam_questions(raw, limit=5)


def test_parse_grades_reads_clamps_and_skips() -> None:
    raw = {
        "grades": [
            {"question_id": "a", "score": 2},  # clamped to 1
            {"question_id": "b", "score": -1},  # clamped to 0
            {"question_id": "c", "score": True},  # bool skipped
            {"question_id": 5, "score": 0.5},  # non-string id skipped
            {"question_id": "e", "score": "high"},  # non-numeric score skipped
            "garbage",  # non-dict skipped
            {"question_id": "d", "score": 0.5},
        ]
    }
    assert parse_grades(raw) == {"a": 1.0, "b": 0.0, "d": 0.5}


def test_parse_grades_rejects_non_list() -> None:
    with pytest.raises(InvalidExam):
        parse_grades({"grades": "nope"})


def _exam() -> MockExam:
    from datetime import UTC, datetime

    return MockExam(
        id="e1",
        owner_id="alice",
        deck_id="d1",
        created_at=datetime(2026, 11, 8, tzinfo=UTC),
        time_limit_seconds=None,
        questions=(
            ExamQuestion(id="q1", prompt="p1", reference="r1", topic="Bio"),
            ExamQuestion(id="q2", prompt="p2", reference="r2", topic="Bio"),
            ExamQuestion(id="q3", prompt="p3", reference="r3", topic="Chem"),
        ),
    )


def test_grade_exam_aggregates_by_topic_and_scores_unanswered_zero() -> None:
    result = grade_exam(_exam(), {"q1": 1.0, "q2": 0.0})
    # q3 was not graded, so it counts as zero.
    assert result.question_scores == (
        QuestionScore("q1", 1.0),
        QuestionScore("q2", 0.0),
        QuestionScore("q3", 0.0),
    )
    assert result.overall_score == pytest.approx(1.0 / 3)
    by_topic = {t.topic: (t.score, t.question_count) for t in result.topics}
    assert by_topic == {"Bio": (0.5, 2), "Chem": (0.0, 1)}


class _ScriptedLLM:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = responses
        self.requests: list[StructuredRequest] = []

    def queue(self, response: dict[str, object]) -> None:
        self._responses.append(response)

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        self.requests.append(request)
        return StructuredResult(
            data=self._responses.pop(0), usage=TokenUsage(1, 1), model="fake"
        )


def _service(
    llm: _ScriptedLLM, *, unlock_cards: int
) -> tuple[MockExamService, DeckService, NoteService]:
    clock = SystemClock()
    decks_repo = InMemoryDeckRepository()
    notes_repo = InMemoryNoteRepository()
    cards_repo = InMemoryCardRepository()
    exams_repo = InMemoryMockExamRepository()
    decks = DeckService(decks_repo, clock)
    notes = NoteService(decks, notes_repo, cards_repo, clock)
    service = MockExamService(
        decks, notes_repo, cards_repo, exams_repo, llm, clock, unlock_cards=unlock_cards
    )
    return service, decks, notes


async def _deck_with_cards(
    decks: DeckService, notes: NoteService, owner: str, count: int
) -> str:
    deck = await decks.create(owner_id=owner, name="Bio")
    for i in range(count):
        await notes.create_note(
            owner_id=owner,
            deck_id=deck.id,
            note_type=NoteType.BASIC,
            fields={"front": f"Q{i}", "back": f"A{i}"},
        )
    return deck.id


async def test_create_is_locked_below_the_threshold() -> None:
    service, decks, notes = _service(_ScriptedLLM([NOW_QUESTIONS]), unlock_cards=3)
    deck_id = await _deck_with_cards(decks, notes, "alice", 2)
    with pytest.raises(ExamLocked):
        await service.create(
            owner_id="alice", deck_id=deck_id, question_count=5, time_limit_seconds=None
        )


async def test_create_stores_exam_with_references_and_sends_material_as_data() -> None:
    llm = _ScriptedLLM([NOW_QUESTIONS])
    service, decks, notes = _service(llm, unlock_cards=1)
    deck_id = await _deck_with_cards(decks, notes, "alice", 1)

    exam = await service.create(
        owner_id="alice", deck_id=deck_id, question_count=2, time_limit_seconds=600
    )

    assert exam.time_limit_seconds == 600
    assert [q.prompt for q in exam.questions] == ["What is a cell?", "Define osmosis"]
    # References are stored server-side.
    assert exam.questions[0].reference == "The unit of life"
    # Deck material went as a data document, not in the instructions.
    assert b"Q0" in llm.requests[0].documents[0].data
    assert "Q0" not in llm.requests[0].instructions


async def test_create_caps_the_question_count() -> None:
    llm = _ScriptedLLM([NOW_QUESTIONS])
    service, decks, notes = _service(llm, unlock_cards=1)
    deck_id = await _deck_with_cards(decks, notes, "alice", 1)
    # Asking for more than the schema returns still only yields what the model gave.
    exam = await service.create(
        owner_id="alice", deck_id=deck_id, question_count=999, time_limit_seconds=None
    )
    assert len(exam.questions) == 2


async def test_create_is_owner_scoped() -> None:
    service, decks, notes = _service(_ScriptedLLM([NOW_QUESTIONS]), unlock_cards=1)
    deck_id = await _deck_with_cards(decks, notes, "alice", 1)
    with pytest.raises(DeckNotFound):
        await service.create(
            owner_id="bob", deck_id=deck_id, question_count=2, time_limit_seconds=None
        )


async def test_grade_scores_answers_and_ignores_unknown_ids() -> None:
    llm = _ScriptedLLM([NOW_QUESTIONS])
    service, decks, notes = _service(llm, unlock_cards=1)
    deck_id = await _deck_with_cards(decks, notes, "alice", 1)
    exam = await service.create(
        owner_id="alice", deck_id=deck_id, question_count=2, time_limit_seconds=None
    )
    first = exam.questions[0].id
    # Queue the grade now that we know a real question id (create consumed its response).
    llm.queue({"grades": [{"question_id": first, "score": 1.0}]})

    result = await service.grade(
        owner_id="alice",
        exam_id=exam.id,
        answers={first: "the unit of life", "bogus-id": "ignored"},
    )

    scores = {qs.question_id: qs.score for qs in result.question_scores}
    assert scores[first] == 1.0
    # The grading request only carried the known answer, not the bogus id.
    assert b"bogus-id" not in llm.requests[1].documents[0].data


async def test_grade_with_no_answers_skips_the_model_and_scores_zero() -> None:
    llm = _ScriptedLLM([NOW_QUESTIONS])  # only the create response; grade makes no call
    service, decks, notes = _service(llm, unlock_cards=1)
    deck_id = await _deck_with_cards(decks, notes, "alice", 1)
    exam = await service.create(
        owner_id="alice", deck_id=deck_id, question_count=2, time_limit_seconds=None
    )

    result = await service.grade(owner_id="alice", exam_id=exam.id, answers={})

    assert result.overall_score == 0.0
    assert len(llm.requests) == 1  # no grading call was made


async def test_create_source_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("flashcards.application.mock_exam_service.MAX_EXAM_SOURCE_CHARS", 5)
    llm = _ScriptedLLM([NOW_QUESTIONS])
    service, decks, notes = _service(llm, unlock_cards=1)
    deck_id = await _deck_with_cards(decks, notes, "alice", 2)
    await service.create(
        owner_id="alice", deck_id=deck_id, question_count=2, time_limit_seconds=None
    )
    assert len(llm.requests[0].documents[0].data) <= 5


async def test_grade_is_owner_scoped() -> None:
    llm = _ScriptedLLM([NOW_QUESTIONS])
    service, decks, notes = _service(llm, unlock_cards=1)
    deck_id = await _deck_with_cards(decks, notes, "alice", 1)
    exam = await service.create(
        owner_id="alice", deck_id=deck_id, question_count=2, time_limit_seconds=None
    )
    with pytest.raises(ExamNotFound):
        await service.grade(owner_id="bob", exam_id=exam.id, answers={})
    with pytest.raises(ExamNotFound):
        await service.grade(owner_id="alice", exam_id="missing", answers={})
