# SPDX-License-Identifier: AGPL-3.0-only
"""Deck routes (F-DECK-1). Domain errors are mapped to HTTP centrally in app.py."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, ai_slot, current_user, get_container
from flashcards.api.schemas import (
    DeckCreateRequest,
    DeckMoveRequest,
    DeckOut,
    DeckRenameRequest,
    DeckSummaryOut,
)
from flashcards.domain.deck_export import MAX_EXPORT_CARDS, ExportFormat, ExportTooLarge

router = APIRouter(prefix="/decks", tags=["decks"])


@router.post("/{deck_id}/summary", response_model=DeckSummaryOut)
async def summarize_deck(
    deck_id: str,
    _slot: None = Depends(ai_slot),  # before the DB session: see ai_slot
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> DeckSummaryOut:
    summary = await container.summary.summarize(owner_id=caller.user.id, deck_id=deck_id)
    return DeckSummaryOut.from_summary(summary)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DeckOut)
async def create_deck(
    body: DeckCreateRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> DeckOut:
    deck = await container.decks.create(
        owner_id=caller.user.id, name=body.name, parent_id=body.parent_id
    )
    return DeckOut.from_deck(deck)


@router.get("", response_model=list[DeckOut])
async def list_decks(
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> list[DeckOut]:
    return [DeckOut.from_deck(d) for d in await container.decks.list_decks(caller.user.id)]


@router.get("/{deck_id}", response_model=DeckOut)
async def get_deck(
    deck_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> DeckOut:
    return DeckOut.from_deck(await container.decks.get(owner_id=caller.user.id, deck_id=deck_id))


@router.patch("/{deck_id}", response_model=DeckOut)
async def rename_deck(
    deck_id: str,
    body: DeckRenameRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> DeckOut:
    deck = await container.decks.rename(
        owner_id=caller.user.id, deck_id=deck_id, name=body.name
    )
    return DeckOut.from_deck(deck)


@router.post("/{deck_id}/move", response_model=DeckOut)
async def move_deck(
    deck_id: str,
    body: DeckMoveRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> DeckOut:
    deck = await container.decks.move(
        owner_id=caller.user.id, deck_id=deck_id, new_parent_id=body.parent_id
    )
    return DeckOut.from_deck(deck)


@router.delete("/{deck_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deck(
    deck_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    await container.library.delete_deck(owner_id=caller.user.id, deck_id=deck_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{deck_id}/export",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}, "text/csv": {}}}},
)
async def export_deck(
    deck_id: str,
    export_format: ExportFormat = Query(default=ExportFormat.APKG, alias="format"),
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    """Download the deck and its subdecks as an Anki .apkg, CSV or JSON (F-IMP-4)."""
    try:
        exported = await container.deck_export.export(
            owner_id=caller.user.id, deck_id=deck_id, export_format=export_format
        )
    except ExportTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"A deck export holds at most {MAX_EXPORT_CARDS} cards.",
        ) from exc
    return Response(
        content=exported.data,
        media_type=exported.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{exported.filename}"',
            "Cache-Control": "no-store",
        },
    )
