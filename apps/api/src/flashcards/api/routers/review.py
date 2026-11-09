# SPDX-License-Identifier: AGPL-3.0-only
"""Review routes: the due queue, grading a card and undoing the last answer
(F-SRS-2, F-STUDY-1, F-STUDY-3).

Editing a card mid-session is PATCH /notes/{note_id}: a card keeps its id,
schedule and history across an edit, so the session carries on with it.
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
    UndoOut,
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
    outcome = await container.reviews.submit(
        owner_id=caller.user.id,
        card_id=card_id,
        rating=body.rating,
        idempotency_key=body.idempotency_key,
    )
    progress = await container.progress.today(owner_id=caller.user.id, with_streak=False)
    return ReviewResultOut.from_outcome(outcome, progress)


@router.post("/reviews/{review_id}/undo", response_model=UndoOut)
async def undo_review(
    review_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> UndoOut:
    outcome = await container.reviews.undo(owner_id=caller.user.id, review_id=review_id)
    progress = await container.progress.today(owner_id=caller.user.id, with_streak=False)
    return UndoOut.from_outcome(outcome, progress)


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
