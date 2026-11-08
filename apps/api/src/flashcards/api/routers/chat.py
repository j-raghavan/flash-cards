# SPDX-License-Identifier: AGPL-3.0-only
"""Document chat: ask a question of a deck's source documents (F-AI-8)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, current_user, get_container
from flashcards.api.schemas import ChatAnswerOut, ChatRequest

router = APIRouter(tags=["chat"])


@router.post("/decks/{deck_id}/chat", response_model=ChatAnswerOut)
async def ask_deck(
    deck_id: str,
    body: ChatRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> ChatAnswerOut:
    answer = await container.chat.ask(
        owner_id=caller.user.id, deck_id=deck_id, question=body.question
    )
    return ChatAnswerOut.from_answer(answer)
