# SPDX-License-Identifier: AGPL-3.0-only
"""Review routes: the due queue and grading a card (F-SRS-2, F-STUDY-1).

Each answer also reports today's progress against the daily goal (F-PROG-1).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, ai_slot, current_user, get_container
from flashcards.api.schemas import (
    CardOut,
    EvaluateRequest,
    EvaluationOut,
    ReviewRequest,
    ReviewResultOut,
)

router = APIRouter(tags=["review"])


@router.get("/decks/{deck_id}/review", response_model=list[CardOut])
async def due_queue(
    deck_id: str,
    limit: int = Query(default=50, gt=0, le=500),
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> list[CardOut]:
    cards = await container.reviews.due_queue(
        owner_id=caller.user.id, deck_id=deck_id, limit=limit
    )
    return [CardOut.from_card(card) for card in cards]


@router.post("/cards/{card_id}/review", response_model=ReviewResultOut)
async def submit_review(
    card_id: str,
    body: ReviewRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> ReviewResultOut:
    schedule = await container.reviews.submit(
        owner_id=caller.user.id,
        card_id=card_id,
        rating=body.rating,
        idempotency_key=body.idempotency_key,
    )
    progress = await container.progress.today(owner_id=caller.user.id, with_streak=False)
    return ReviewResultOut.from_schedule_and_progress(schedule, progress)


@router.post("/cards/{card_id}/evaluate", response_model=EvaluationOut)
async def evaluate_answer(
    card_id: str,
    body: EvaluateRequest,
    _slot: None = Depends(ai_slot),  # before the DB session: see ai_slot
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> EvaluationOut:
    evaluation = await container.evaluation.evaluate(
        owner_id=caller.user.id, card_id=card_id, answer=body.answer
    )
    return EvaluationOut.from_evaluation(evaluation)
