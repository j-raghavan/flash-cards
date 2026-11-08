# SPDX-License-Identifier: AGPL-3.0-only
"""Mock exams: an AI-composed question set graded with a per-topic breakdown (F-AI-7).

The exam is generated and stored server-side with its reference answers, so the
client never sees the answers before grading. Grading maps per-question scores
back onto the stored exam and aggregates them by topic. The schemas and parsers
live here so the domain owns the model's output contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from flashcards.domain.ai import LlmError

# Default unlock threshold (R19: mock exams unlock at >= 20 cards).
DEFAULT_MOCK_EXAM_UNLOCK_CARDS = 20
DEFAULT_EXAM_QUESTIONS = 10
MAX_EXAM_QUESTIONS = 50
EXAM_MAX_OUTPUT_TOKENS = 4096
GRADE_MAX_OUTPUT_TOKENS = 2048
MAX_EXAM_SOURCE_CHARS = 100_000
_DEFAULT_TOPIC = "General"


class ExamLocked(Exception):
    """The deck does not have enough cards for a mock exam yet."""

    def __init__(self, *, required: int, have: int) -> None:
        self.required = required
        self.have = have
        super().__init__(f"a mock exam needs at least {required} cards; the deck has {have}")


class ExamNotFound(Exception):
    """A mock exam that does not exist or is not the caller's."""


class InvalidExam(LlmError):
    """The model's exam or grades did not match the schema."""


@dataclass(frozen=True, slots=True)
class ExamQuestion:
    id: str
    prompt: str
    reference: str
    topic: str


@dataclass(frozen=True, slots=True)
class MockExam:
    id: str
    owner_id: str
    deck_id: str
    created_at: datetime
    time_limit_seconds: int | None
    questions: tuple[ExamQuestion, ...]


@dataclass(frozen=True, slots=True)
class TopicScore:
    topic: str
    score: float
    question_count: int


@dataclass(frozen=True, slots=True)
class QuestionScore:
    question_id: str
    score: float


@dataclass(frozen=True, slots=True)
class ExamResult:
    overall_score: float
    topics: tuple[TopicScore, ...]
    question_scores: tuple[QuestionScore, ...]


EXAM_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "prompt": {"type": "string"},
                    "answer": {"type": "string"},
                    "topic": {"type": "string"},
                },
                "required": ["prompt", "answer", "topic"],
            },
        }
    },
    "required": ["questions"],
}

GRADE_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "grades": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "question_id": {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["question_id", "score"],
            },
        }
    },
    "required": ["grades"],
}


def parse_exam_questions(raw: Mapping[str, object], *, limit: int) -> list[tuple[str, str, str]]:
    """Return up to `limit` (prompt, answer, topic) triples, or raise InvalidExam."""
    questions = raw.get("questions")
    if not isinstance(questions, list) or not questions:
        raise InvalidExam("the exam has no questions")
    out: list[tuple[str, str, str]] = []
    for item in questions[:limit]:
        if not isinstance(item, dict):
            raise InvalidExam("malformed exam question")
        prompt = item.get("prompt")
        answer = item.get("answer")
        topic = item.get("topic")
        if not isinstance(prompt, str) or not prompt.strip():
            raise InvalidExam("exam question is missing a prompt")
        if not isinstance(answer, str) or not answer.strip():
            raise InvalidExam("exam question is missing an answer")
        topic_text = topic.strip() if isinstance(topic, str) and topic.strip() else _DEFAULT_TOPIC
        out.append((prompt.strip(), answer.strip(), topic_text))
    return out


def parse_grades(raw: Mapping[str, object]) -> dict[str, float]:
    """Map question_id -> clamped score. Malformed entries are skipped, not fatal."""
    grades = raw.get("grades")
    if not isinstance(grades, list):
        raise InvalidExam("grades are missing or not a list")
    result: dict[str, float] = {}
    for item in grades:
        if not isinstance(item, dict):
            continue
        question_id = item.get("question_id")
        score = item.get("score")
        if not isinstance(question_id, str) or isinstance(score, bool):
            continue
        if isinstance(score, int | float):
            result[question_id] = min(1.0, max(0.0, float(score)))
    return result


def grade_exam(exam: MockExam, scores_by_id: Mapping[str, float]) -> ExamResult:
    """Project per-question scores onto the exam and aggregate by topic.

    A question with no score (unanswered or ungraded) counts as zero, so an
    incomplete attempt is scored against the whole exam, not just what was tried.
    """
    question_scores = tuple(
        QuestionScore(question_id=q.id, score=scores_by_id.get(q.id, 0.0))
        for q in exam.questions
    )
    overall = _mean([qs.score for qs in question_scores])
    by_topic: dict[str, list[float]] = {}
    for question in exam.questions:
        by_topic.setdefault(question.topic, []).append(scores_by_id.get(question.id, 0.0))
    topics = tuple(
        TopicScore(topic=topic, score=_mean(scores), question_count=len(scores))
        for topic, scores in by_topic.items()
    )
    return ExamResult(overall_score=overall, topics=topics, question_scores=question_scores)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
