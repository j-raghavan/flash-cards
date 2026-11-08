# SPDX-License-Identifier: AGPL-3.0-only
"""Mock-exam routes: generate a question set, then grade a submission (F-AI-7)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, ai_slot, current_user, get_container
from flashcards.api.schemas import (
    ExamResultOut,
    ExamSubmitRequest,
    MockExamCreateRequest,
    MockExamOut,
)

router = APIRouter(tags=["mock-exam"])


@router.post(
    "/decks/{deck_id}/mock-exam",
    status_code=status.HTTP_201_CREATED,
    response_model=MockExamOut,
)
async def create_mock_exam(
    deck_id: str,
    body: MockExamCreateRequest,
    _slot: None = Depends(ai_slot),  # before the DB session: see ai_slot
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> MockExamOut:
    exam = await container.mock_exam.create(
        owner_id=caller.user.id,
        deck_id=deck_id,
        question_count=body.question_count,
        time_limit_seconds=body.time_limit_seconds,
    )
    return MockExamOut.from_exam(exam)


@router.post("/mock-exams/{exam_id}/submit", response_model=ExamResultOut)
async def submit_mock_exam(
    exam_id: str,
    body: ExamSubmitRequest,
    _slot: None = Depends(ai_slot),  # before the DB session: see ai_slot
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> ExamResultOut:
    result = await container.mock_exam.grade(
        owner_id=caller.user.id,
        exam_id=exam_id,
        answers={answer.question_id: answer.answer for answer in body.answers},
    )
    return ExamResultOut.from_result(result)
