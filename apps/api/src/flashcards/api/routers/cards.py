# SPDX-License-Identifier: AGPL-3.0-only
"""Note, card and deck-stats routes (F-CARD-*, F-PROG-2/4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, current_user, get_container
from flashcards.api.schemas import (
    CardDetailOut,
    CardOut,
    DeckStatsOut,
    NoteCreateRequest,
)

router = APIRouter(tags=["cards"])


@router.post(
    "/decks/{deck_id}/notes", status_code=status.HTTP_201_CREATED, response_model=list[CardOut]
)
async def create_note(
    deck_id: str,
    body: NoteCreateRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> list[CardOut]:
    _note, cards = await container.notes.create_note(
        owner_id=caller.user.id, deck_id=deck_id, note_type=body.note_type, fields=body.fields
    )
    return [CardOut.from_card(card) for card in cards]


@router.get("/decks/{deck_id}/cards", response_model=list[CardOut])
async def list_cards(
    deck_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> list[CardOut]:
    cards = await container.notes.list_cards(owner_id=caller.user.id, deck_id=deck_id)
    return [CardOut.from_card(card) for card in cards]


@router.get("/decks/{deck_id}/stats", response_model=DeckStatsOut)
async def deck_stats(
    deck_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> DeckStatsOut:
    stats = await container.reviews.deck_stats(owner_id=caller.user.id, deck_id=deck_id)
    return DeckStatsOut.from_stats(stats)


@router.get("/cards/{card_id}", response_model=CardDetailOut)
async def get_card(
    card_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> CardDetailOut:
    card = await container.notes.get_card(owner_id=caller.user.id, card_id=card_id)
    note = await container.notes.get_note(owner_id=caller.user.id, note_id=card.note_id)
    return CardDetailOut.from_card_and_note(card, note)


@router.post("/cards/{card_id}/suspend", response_model=CardOut)
async def suspend_card(
    card_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> CardOut:
    return CardOut.from_card(
        await container.notes.suspend_card(owner_id=caller.user.id, card_id=card_id)
    )


@router.post("/cards/{card_id}/unsuspend", response_model=CardOut)
async def unsuspend_card(
    card_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> CardOut:
    return CardOut.from_card(
        await container.notes.unsuspend_card(owner_id=caller.user.id, card_id=card_id)
    )


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    note_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    await container.library.delete_note(owner_id=caller.user.id, note_id=note_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
