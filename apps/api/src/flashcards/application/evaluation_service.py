# SPDX-License-Identifier: AGPL-3.0-only
"""AI-evaluation use case: grade a typed answer against a card (F-STUDY-2).

The card's content (the grading key) and the student's typed answer both go to the
model as data documents, never concatenated into the instructions (NF-SEC-6).
The grade is advisory: it does not touch scheduling, which stays with review.
"""

from __future__ import annotations

from flashcards.application.ai_budget import AiCallBudget
from flashcards.application.ai_prompts import evaluation_prompt
from flashcards.application.note_service import NoteService
from flashcards.domain.ai import LlmDocument, StructuredRequest
from flashcards.domain.evaluation import (
    EVALUATION_MAX_OUTPUT_TOKENS,
    EVALUATION_SCHEMA,
    AnswerEvaluation,
    parse_evaluation,
)
from flashcards.domain.notes import Fields, InvalidNote, NoteType
from flashcards.domain.ports import LLMProvider


class EvaluationService:
    def __init__(self, notes: NoteService, llm: LLMProvider, budget: AiCallBudget) -> None:
        self._notes = notes
        self._llm = llm
        self._budget = budget

    async def evaluate(
        self, *, owner_id: str, card_id: str, answer: str
    ) -> AnswerEvaluation:
        # Both calls are owner-scoped and raise CardNotFound / NoteNotFound.
        card = await self._notes.get_card(owner_id=owner_id, card_id=card_id)
        note = await self._notes.get_note(owner_id=owner_id, note_id=card.note_id)
        if note.note_type is NoteType.IMAGE_OCCLUSION:
            # The answer is a region of an image the model never sees (ADR 0013).
            raise InvalidNote("image occlusion cards cannot be graded from a typed answer")
        request = StructuredRequest(
            instructions=evaluation_prompt(),
            documents=(
                LlmDocument(
                    media_type="text/plain", data=_reference_text(note.fields).encode("utf-8")
                ),
                LlmDocument(media_type="text/plain", data=answer.encode("utf-8")),
            ),
            schema=EVALUATION_SCHEMA,
            max_output_tokens=EVALUATION_MAX_OUTPUT_TOKENS,
        )
        await self._budget.charge(owner_id)  # raises AiCallLimitReached
        result = await self._llm.generate_structured(request)
        return parse_evaluation(result.data)


def _reference_text(fields: Fields) -> str:
    return "\n".join(f"{name}: {value}" for name, value in fields)
