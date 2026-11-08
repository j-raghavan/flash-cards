# SPDX-License-Identifier: AGPL-3.0-only
"""Deck share links: manage links, preview and clone shared decks (F-SHARE-1..3)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, ai_slot, current_user, get_container
from flashcards.api.schemas import (
    CreatedShareLinkOut,
    DeckOut,
    ShareCreateRequest,
    SharedDeckOut,
    ShareLinkOut,
    ShareUpdateRequest,
)

router = APIRouter(tags=["sharing"])


@router.post(
    "/decks/{deck_id}/shares",
    status_code=status.HTTP_201_CREATED,
    response_model=CreatedShareLinkOut,
)
async def create_share_link(
    deck_id: str,
    body: ShareCreateRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> CreatedShareLinkOut:
    created = await container.sharing.create(
        owner_id=caller.user.id, deck_id=deck_id, allow_clone=body.allow_clone
    )
    link = ShareLinkOut.from_link(created.link)
    return CreatedShareLinkOut(**link.model_dump(), token=created.raw_token)


@router.get("/decks/{deck_id}/shares", response_model=list[ShareLinkOut])
async def list_share_links(
    deck_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> list[ShareLinkOut]:
    links = await container.sharing.list_for_deck(owner_id=caller.user.id, deck_id=deck_id)
    return [ShareLinkOut.from_link(link) for link in links]


@router.patch("/shares/{share_id}", response_model=ShareLinkOut)
async def update_share_link(
    share_id: str,
    body: ShareUpdateRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> ShareLinkOut:
    link = await container.sharing.set_allow_clone(
        owner_id=caller.user.id, link_id=share_id, allow=body.allow_clone
    )
    return ShareLinkOut.from_link(link)


@router.post("/shares/{share_id}/revoke", response_model=ShareLinkOut)
async def revoke_share_link(
    share_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> ShareLinkOut:
    link = await container.sharing.revoke(owner_id=caller.user.id, link_id=share_id)
    return ShareLinkOut.from_link(link)


@router.get("/shared/{token}", response_model=SharedDeckOut)
async def preview_shared_deck(
    token: str,
    _caller: CurrentUser = Depends(current_user),  # signed-in accounts only (invite-only)
    container: Container = Depends(get_container, scope="function"),
) -> SharedDeckOut:
    return SharedDeckOut.from_preview(await container.sharing.preview(token=token))


@router.post(
    "/shared/{token}/clone", status_code=status.HTTP_201_CREATED, response_model=DeckOut
)
async def clone_shared_deck(
    token: str,
    # A clone writes many rows in one request; share the bounded slot pool so a
    # burst of clones cannot take every database connection.
    _slot: None = Depends(ai_slot),
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> DeckOut:
    deck = await container.sharing.clone(owner_id=caller.user.id, token=token)
    return DeckOut.from_deck(deck)
