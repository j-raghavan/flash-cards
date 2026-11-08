# SPDX-License-Identifier: AGPL-3.0-only
"""The mock-exam parsers, grader and service (in-memory, scripted LLM)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryMockExamRepository,
    InMemoryNoteRepository,
    InMemoryQuotaRepository,
)
from flashcards.application.ai_budget import AiCallBudget
from flashcards.application.deck_service import DeckService
from flashcards.application.mock_exam_service import MockExamService
from flashcards.application.note_service import NoteService
from flashcards.domain.ai import StructuredRequest, StructuredResult, TokenUsage
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.mock_exam import (
    SUBMIT_GRACE_SECONDS,
    ExamClosed,
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


def test_parse_grades_reads_numbers_clamps_and_skips() -> None:
    raw = {
        "grades": [
            {"question": 1, "score": 2},  # clamped to 1
            {"question": 2, "score": -1},  # clamped to 0
            {"question": 3, "score": True},  # bool score skipped
            {"question": True, "score": 0.5},  # bool number skipped
            {"question": "4", "score": 0.5},  # non-integer number skipped
            {"question": 5, "score": "high"},  # non-numeric score skipped
            "garbage",  # non-dict skipped
            {"question": 6, "score": 0.5},
        ]
    }
    assert parse_grades(raw) == {1: 1.0, 2: 0.0, 6: 0.5}


def test_parse_grades_rejects_non_list() -> None:
    with pytest.raises(InvalidExam):
        parse_grades({"grades": "nope"})


def _exam() -> MockExam:
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


class _Clock:
    def __init__(self) -> None:
        self._now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: int) -> None:
        self._now += timedelta(seconds=seconds)


def _service(
    llm: _ScriptedLLM, *, unlock_cards: int, clock: _Clock | None = None
) -> tuple[MockExamService, DeckService, NoteService]:
    clock = clock or _Clock()
    decks_repo = InMemoryDeckRepository()
    notes_repo = InMemoryNoteRepository()
    cards_repo = InMemoryCardRepository()
    exams_repo = InMemoryMockExamRepository()
    decks = DeckService(decks_repo, clock)
    notes = NoteService(decks, notes_repo, cards_repo, clock)
    budget = AiCallBudget(InMemoryQuotaRepository(limit=100), clock)
    service = MockExamService(
        decks, notes_repo, cards_repo, exams_repo, llm, budget, clock, unlock_cards=unlock_cards
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


async def test_grade_scores_only_what_was_answered() -> None:
    llm = _ScriptedLLM([NOW_QUESTIONS])
    service, decks, notes = _service(llm, unlock_cards=1)
    deck_id = await _deck_with_cards(decks, notes, "alice", 1)
    exam = await service.create(
        owner_id="alice", deck_id=deck_id, question_count=2, time_limit_seconds=None
    )
    first, second = (q.id for q in exam.questions)
    # The model also "grades" question 2, which the student left blank: it must not count.
    llm.queue({"grades": [{"question": 1, "score": 1.0}, {"question": 2, "score": 0.9}]})

    result = await service.grade(
        owner_id="alice",
        exam_id=exam.id,
        answers={first: "the unit of life", second: "   ", "bogus-id": "ignored"},
    )

    scores = {qs.question_id: qs.score for qs in result.question_scores}
    assert scores == {first: 1.0, second: 0.0}
    assert result.overall_score == 0.5
    request = llm.requests[1]
    # NF-SEC-6: the grading key and the answers are data documents, numbered.
    assert request.documents[0].data.startswith(b"[1] (topic: Biology) What is a cell?")
    assert b"The unit of life" in request.documents[0].data
    assert json.loads(request.documents[1].data) == {"1": "the unit of life"}
    assert "unit of life" not in request.instructions


async def test_an_exam_is_submitted_only_once() -> None:
    llm = _ScriptedLLM([NOW_QUESTIONS])
    service, decks, notes = _service(llm, unlock_cards=1)
    deck_id = await _deck_with_cards(decks, notes, "alice", 1)
    exam = await service.create(
        owner_id="alice", deck_id=deck_id, question_count=2, time_limit_seconds=None
    )
    await service.grade(owner_id="alice", exam_id=exam.id, answers={})
    with pytest.raises(ExamClosed, match="already submitted"):
        await service.grade(owner_id="alice", exam_id=exam.id, answers={})


async def test_the_time_limit_is_enforced_with_a_grace_period() -> None:
    clock = _Clock()
    llm = _ScriptedLLM([NOW_QUESTIONS, NOW_QUESTIONS])
    service, decks, notes = _service(llm, unlock_cards=1, clock=clock)
    deck_id = await _deck_with_cards(decks, notes, "alice", 1)
    on_time = await service.create(
        owner_id="alice", deck_id=deck_id, question_count=2, time_limit_seconds=60
    )
    late = await service.create(
        owner_id="alice", deck_id=deck_id, question_count=2, time_limit_seconds=60
    )
    assert on_time.deadline == on_time.created_at + timedelta(seconds=60)

    clock.advance(60 + SUBMIT_GRACE_SECONDS)  # exactly at the end of the grace period
    await service.grade(owner_id="alice", exam_id=on_time.id, answers={})
    clock.advance(1)
    with pytest.raises(ExamClosed, match="time limit"):
        await service.grade(owner_id="alice", exam_id=late.id, answers={})


async def test_mark_submitted_is_atomic_in_memory() -> None:
    repo = InMemoryMockExamRepository()
    exam = _exam()
    await repo.add(exam)
    assert await repo.try_mark_submitted(exam.id, exam.created_at) is True
    assert await repo.try_mark_submitted(exam.id, exam.created_at) is False
    assert await repo.try_mark_submitted("missing", exam.created_at) is False
    assert [e.id for e in await repo.list_for_owner("alice")] == ["e1"]


async def test_create_counts_subdeck_cards() -> None:
    llm = _ScriptedLLM([NOW_QUESTIONS])
    service, decks, notes = _service(llm, unlock_cards=2)
    parent = await decks.create(owner_id="alice", name="Parent")
    child_deck = await _deck_with_cards(decks, notes, "alice", 2)
    await decks.move(owner_id="alice", deck_id=child_deck, new_parent_id=parent.id)
    exam = await service.create(
        owner_id="alice", deck_id=parent.id, question_count=2, time_limit_seconds=None
    )
    assert exam.deck_id == parent.id
    assert b"Q0" in llm.requests[0].documents[0].data  # the subdeck's notes are the source


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


class _LosesTheRace(InMemoryMockExamRepository):
    """Reads as unsubmitted, but a concurrent request records its submission first."""

    async def try_mark_submitted(self, exam_id: str, now: datetime) -> bool:
        return False


async def test_a_concurrent_submission_wins() -> None:
    clock = _Clock()
    decks_repo, notes_repo, cards_repo = (
        InMemoryDeckRepository(),
        InMemoryNoteRepository(),
        InMemoryCardRepository(),
    )
    decks = DeckService(decks_repo, clock)
    exams = _LosesTheRace()
    budget = AiCallBudget(InMemoryQuotaRepository(limit=100), clock)
    service = MockExamService(
        decks, notes_repo, cards_repo, exams, _ScriptedLLM([]), budget, clock, unlock_cards=1
    )
    await exams.add(_exam())
    with pytest.raises(ExamClosed, match="already submitted"):
        await service.grade(owner_id="alice", exam_id="e1", answers={})
