# SPDX-License-Identifier: AGPL-3.0-only
"""Mock-exam use cases: generate a question set, then grade a submission (F-AI-7).

Generation is gated on a card threshold and stores the exam (with its reference
answers) server-side, so the client never receives the answers. Deck material
and the student's answers are sent to the model as data documents, never
concatenated into the instructions (NF-SEC-6).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping

from flashcards.application.ai_prompts import exam_grading_prompt, exam_prompt
from flashcards.application.deck_service import DeckService
from flashcards.domain.ai import LlmDocument, StructuredRequest
from flashcards.domain.mock_exam import (
    EXAM_MAX_OUTPUT_TOKENS,
    EXAM_SCHEMA,
    GRADE_MAX_OUTPUT_TOKENS,
    GRADE_SCHEMA,
    MAX_EXAM_QUESTIONS,
    MAX_EXAM_SOURCE_CHARS,
    ExamLocked,
    ExamNotFound,
    ExamQuestion,
    ExamResult,
    MockExam,
    grade_exam,
    parse_exam_questions,
    parse_grades,
)
from flashcards.domain.notes import Note
from flashcards.domain.ports import (
    CardRepository,
    Clock,
    LLMProvider,
    MockExamRepository,
    NoteRepository,
)


class MockExamService:
    def __init__(
        self,
        decks: DeckService,
        notes: NoteRepository,
        cards: CardRepository,
        exams: MockExamRepository,
        llm: LLMProvider,
        clock: Clock,
        *,
        unlock_cards: int,
    ) -> None:
        self._decks = decks
        self._notes = notes
        self._cards = cards
        self._exams = exams
        self._llm = llm
        self._clock = clock
        self._unlock_cards = unlock_cards

    async def create(
        self,
        *,
        owner_id: str,
        deck_id: str,
        question_count: int,
        time_limit_seconds: int | None,
    ) -> MockExam:
        # Raises DeckNotFound if the deck is missing or not the caller's.
        await self._decks.get(owner_id=owner_id, deck_id=deck_id)
        card_count = len(await self._cards.list_for_deck(deck_id))
        if card_count < self._unlock_cards:
            raise ExamLocked(required=self._unlock_cards, have=card_count)
        limit = max(1, min(question_count, MAX_EXAM_QUESTIONS))
        source = _collect_source(await self._notes.list_for_deck(deck_id))
        request = StructuredRequest(
            instructions=exam_prompt(limit),
            documents=(LlmDocument(media_type="text/plain", data=source.encode("utf-8")),),
            schema=EXAM_SCHEMA,
            max_output_tokens=EXAM_MAX_OUTPUT_TOKENS,
        )
        result = await self._llm.generate_structured(request)
        questions = tuple(
            ExamQuestion(id=str(uuid.uuid4()), prompt=prompt, reference=answer, topic=topic)
            for prompt, answer, topic in parse_exam_questions(result.data, limit=limit)
        )
        exam = MockExam(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            deck_id=deck_id,
            created_at=self._clock.now(),
            time_limit_seconds=time_limit_seconds,
            questions=questions,
        )
        await self._exams.add(exam)
        return exam

    async def grade(
        self, *, owner_id: str, exam_id: str, answers: Mapping[str, str]
    ) -> ExamResult:
        exam = await self._exams.get(exam_id)
        if exam is None or exam.owner_id != owner_id:
            raise ExamNotFound(exam_id)
        valid_ids = {question.id for question in exam.questions}
        submitted = {qid: text for qid, text in answers.items() if qid in valid_ids}
        if not submitted:
            # Nothing to grade: every question scores zero.
            return grade_exam(exam, {})
        request = StructuredRequest(
            instructions=exam_grading_prompt(_questions_text(exam.questions)),
            documents=(
                LlmDocument(
                    media_type="text/plain", data=json.dumps(submitted).encode("utf-8")
                ),
            ),
            schema=GRADE_SCHEMA,
            max_output_tokens=GRADE_MAX_OUTPUT_TOKENS,
        )
        result = await self._llm.generate_structured(request)
        return grade_exam(exam, parse_grades(result.data))


def _collect_source(notes: list[Note]) -> str:
    lines: list[str] = []
    total = 0
    for note in notes:
        for name, value in note.fields:
            line = f"{name}: {value}"
            total += len(line) + 1
            if total > MAX_EXAM_SOURCE_CHARS:
                return "\n".join(lines)
            lines.append(line)
    return "\n".join(lines)


def _questions_text(questions: tuple[ExamQuestion, ...]) -> str:
    return "\n".join(
        f"[{q.id}] (topic: {q.topic}) {q.prompt}\nreference: {q.reference}" for q in questions
    )
