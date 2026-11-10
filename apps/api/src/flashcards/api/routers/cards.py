# SPDX-License-Identifier: AGPL-3.0-only
"""Note, card and deck-stats routes (F-CARD-*, F-PROG-2/4)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, current_user, get_container
from flashcards.api.schemas import (
    BrowserPageOut,
    BrowserRowOut,
    BulkCardRequest,
    BulkResultOut,
    CardDetailOut,
    CardOut,
    DeckStatsOut,
    NoteCreateRequest,
    NoteOut,
    NoteUpdateRequest,
)
from flashcards.domain.browser import (
    MAX_OFFSET,
    MAX_PAGE,
    MAX_QUERY_LENGTH,
    BrowserQuery,
    CardState,
    SortKey,
)
from flashcards.domain.notes import MAX_FLAG
from flashcards.domain.tags import MAX_TAG_LENGTH

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
    _note, cards = await container.note_types.create_note(
        owner_id=caller.user.id,
        deck_id=deck_id,
        note_type=body.note_type,
        fields=body.fields,
        tags=body.tags,
        note_type_id=body.note_type_id,
        occlusion=body.occlusion.to_domain() if body.occlusion is not None else None,
    )
    return [CardOut.from_card(card) for card in cards]


@router.get("/decks/{deck_id}/cards", response_model=list[CardOut])
async def list_cards(
    deck_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> list[CardOut]:
    cards = await container.read_access.deck_cards(caller.user.id, deck_id)
    return [CardOut.from_card(card.seen_by(caller.user.id)) for card in cards]


@router.get("/decks/{deck_id}/stats", response_model=DeckStatsOut)
async def deck_stats(
    deck_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> DeckStatsOut:
    stats = await container.reviews.deck_stats(owner_id=caller.user.id, deck_id=deck_id)
    return DeckStatsOut.from_stats(stats)


@router.get("/cards", response_model=BrowserPageOut)
async def browse_cards(
    deck_id: str | None = Query(default=None, description="Limit to this deck."),
    include_subdecks: bool = Query(default=True),
    state: list[CardState] = Query(default_factory=list),
    tag: str | None = Query(default=None, max_length=MAX_TAG_LENGTH),
    flag: int | None = Query(default=None, ge=0, le=MAX_FLAG),
    min_proficiency: int = Query(default=0, ge=0, le=100),
    max_proficiency: int = Query(default=100, ge=0, le=100),
    q: str | None = Query(default=None, max_length=MAX_QUERY_LENGTH),
    sort: SortKey = Query(default=SortKey.CREATED),
    order: Literal["asc", "desc"] = Query(default="asc"),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> BrowserPageOut:
    if min_proficiency > max_proficiency:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "min_proficiency cannot be greater than max_proficiency.",
        )
    query = BrowserQuery(
        states=frozenset(state),
        tag=tag or None,  # an empty tag means no tag filter, as an empty q does
        flag=flag,
        min_proficiency=min_proficiency,
        max_proficiency=max_proficiency,
        text=q or None,
        sort=sort,
        descending=order == "desc",
        limit=limit,
        offset=offset,
    )
    page = await container.card_browser.search(
        owner_id=caller.user.id, query=query, deck_id=deck_id, include_subdecks=include_subdecks
    )
    return BrowserPageOut(
        total=page.total, cards=[BrowserRowOut.from_row(row) for row in page.rows]
    )


@router.post("/cards/bulk", response_model=BulkResultOut)
async def bulk_cards(
    body: BulkCardRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> BulkResultOut:
    result = await container.bulk_cards.apply(
        owner_id=caller.user.id,
        card_ids=body.card_ids,
        action=body.action,
        deck_id=body.deck_id,
        tags=body.tags,
        flag=body.flag or 0,
        until=body.until,
    )
    return BulkResultOut(cards=result.cards, notes=result.notes)


@router.get("/cards/{card_id}", response_model=CardDetailOut)
async def get_card(
    card_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> CardDetailOut:
    card, note, template = await container.read_access.render_card(caller.user.id, card_id)
    return CardDetailOut.from_card_and_note(card.seen_by(caller.user.id), note, template)


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


@router.get("/notes/{note_id}", response_model=NoteOut)
async def get_note(
    note_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> NoteOut:
    note = await container.read_access.note(caller.user.id, note_id)
    cards = await container.notes.cards_of(note)
    return NoteOut.from_note(note, [card.seen_by(caller.user.id) for card in cards])


@router.patch("/notes/{note_id}", response_model=NoteOut)
async def update_note(
    note_id: str,
    body: NoteUpdateRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> NoteOut:
    note, cards = await container.note_types.update_note(
        owner_id=caller.user.id,
        note_id=note_id,
        fields=body.fields,
        tags=body.tags,
        occlusion=body.occlusion.to_domain() if body.occlusion is not None else None,
    )
    return NoteOut.from_note(note, cards)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    note_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    await container.library.delete_note(owner_id=caller.user.id, note_id=note_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
