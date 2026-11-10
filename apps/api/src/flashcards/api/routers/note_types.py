# SPDX-License-Identifier: AGPL-3.0-only
"""Custom note type routes (F-CARD-4, ADR 0013). Owner-scoped."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, current_user, get_container
from flashcards.api.note_type_schemas import (
    NoteTypeCreateRequest,
    NoteTypeOut,
    NoteTypeUpdateRequest,
)

router = APIRouter(prefix="/note-types", tags=["note-types"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=NoteTypeOut)
async def create_note_type(
    body: NoteTypeCreateRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> NoteTypeOut:
    note_type = await container.note_types.create(
        owner_id=caller.user.id,
        name=body.name,
        fields=body.fields,
        templates=[t.to_domain() for t in body.templates],
    )
    return NoteTypeOut.from_note_type(note_type)


@router.get("", response_model=list[NoteTypeOut])
async def list_note_types(
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> list[NoteTypeOut]:
    note_types = await container.note_types.list_types(owner_id=caller.user.id)
    return [NoteTypeOut.from_note_type(t) for t in note_types]


@router.get("/{note_type_id}", response_model=NoteTypeOut)
async def get_note_type(
    note_type_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> NoteTypeOut:
    # Readable for library notes too, so their cards render (ADR 0024).
    note_type = await container.read_access.note_type(caller.user.id, note_type_id)
    return NoteTypeOut.from_note_type(note_type)


@router.patch("/{note_type_id}", response_model=NoteTypeOut)
async def update_note_type(
    note_type_id: str,
    body: NoteTypeUpdateRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> NoteTypeOut:
    note_type = await container.note_types.update(
        owner_id=caller.user.id,
        note_type_id=note_type_id,
        name=body.name,
        fields=body.fields,
        renamed=body.renamed,
        templates=None if body.templates is None else [t.to_domain() for t in body.templates],
    )
    return NoteTypeOut.from_note_type(note_type)


@router.delete("/{note_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note_type(
    note_type_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    await container.note_types.delete(owner_id=caller.user.id, note_type_id=note_type_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
