# SPDX-License-Identifier: AGPL-3.0-only
"""Review routes: the due queue and grading a card (F-SRS-2, F-STUDY-1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, current_user, get_container
from flashcards.api.schemas import CardOut, CardScheduleOut, ReviewRequest

router = APIRouter(tags=["review"])


@router.get("/decks/{deck_id}/review", response_model=list[CardOut])
async def due_queue(
    deck_id: str,
    limit: int = Query(default=50, gt=0, le=500),
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> list[CardOut]:
    cards = await container.reviews.due_queue(
        owner_id=caller.user.id, deck_id=deck_id, limit=limit
    )
    return [CardOut.from_card(card) for card in cards]


@router.post("/cards/{card_id}/review", response_model=CardScheduleOut)
async def submit_review(
    card_id: str,
    body: ReviewRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> CardScheduleOut:
    schedule = await container.reviews.submit(
        owner_id=caller.user.id,
        card_id=card_id,
        rating=body.rating,
        idempotency_key=body.idempotency_key,
    )
    return CardScheduleOut.from_schedule(schedule)
