# SPDX-License-Identifier: AGPL-3.0-only
"""AI-evaluation of a typed answer against a card's reference (F-STUDY-2).

The schema and the parser live here so the domain owns the contract the model
must satisfy. The model's output is re-validated before anything is trusted.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass

from flashcards.domain.ai import InvalidModelOutput

MAX_ANSWER_LEN = 20_000
EVALUATION_MAX_OUTPUT_TOKENS = 1024


class Verdict(enum.StrEnum):
    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"


class InvalidEvaluation(InvalidModelOutput):
    """The model's grade did not match the evaluation schema."""


@dataclass(frozen=True, slots=True)
class AnswerEvaluation:
    verdict: Verdict
    score: float
    explanation: str


EVALUATION_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["correct", "partial", "incorrect"]},
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "explanation": {"type": "string"},
    },
    "required": ["verdict", "score", "explanation"],
}


def parse_evaluation(raw: Mapping[str, object]) -> AnswerEvaluation:
    verdict_raw = raw.get("verdict")
    if not isinstance(verdict_raw, str):
        raise InvalidEvaluation("evaluation is missing a verdict")
    try:
        verdict = Verdict(verdict_raw)
    except ValueError as exc:
        raise InvalidEvaluation(f"unknown verdict: {verdict_raw!r}") from exc

    score_raw = raw.get("score")
    # A JSON bool is an int subclass but never a valid score; reject it explicitly.
    if isinstance(score_raw, bool) or not isinstance(score_raw, int | float):
        raise InvalidEvaluation("evaluation score is missing or not a number")
    score = min(1.0, max(0.0, float(score_raw)))

    explanation = raw.get("explanation")
    if not isinstance(explanation, str):
        raise InvalidEvaluation("evaluation is missing an explanation")
    return AnswerEvaluation(verdict=verdict, score=score, explanation=explanation.strip())
