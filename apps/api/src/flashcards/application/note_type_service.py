# SPDX-License-Identifier: AGPL-3.0-only
"""Custom note types and the notes that need more than fields (F-CARD-3, F-CARD-4).

Note types are owner-scoped. Editing a type's fields or templates re-derives the
cards of every note of the type, the way editing a note does (ADR 0011): a card
whose template survives keeps its schedule and history.

Notes are created and edited through here so a custom note gets its type (locked,
so a concurrent type edit never misses it) and an image occlusion note's image is
checked to be one of the owner's own images.

Locks are always taken type first, then notes, so a type edit and a note edit
never wait on each other in opposite orders.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence

from flashcards.application.library_service import LibraryService
from flashcards.application.note_service import NoteService
from flashcards.domain.media import MediaNotFound
from flashcards.domain.note_types import (
    MAX_NOTE_TYPES_PER_OWNER,
    CustomNoteType,
    InvalidNoteType,
    NoteTypeInUse,
    NoteTypeNotFound,
    TemplateSpec,
    TooManyNoteTypes,
    edit_note_type,
    new_note_type,
    remap_fields,
)
from flashcards.domain.notes import Card, InvalidNote, Note, NoteType, Occlusion
from flashcards.domain.ports import Clock, MediaRepository, NoteRepository, NoteTypeRepository


class NoteTypeService:
    def __init__(
        self,
        note_types: NoteTypeRepository,
        note_repo: NoteRepository,
        notes: NoteService,
        library: LibraryService,
        media: MediaRepository,
        clock: Clock,
    ) -> None:
        self._note_types = note_types
        self._note_repo = note_repo
        self._notes = notes
        self._library = library
        self._media = media
        self._clock = clock

    async def create(
        self,
        *,
        owner_id: str,
        name: str,
        fields: Sequence[str],
        templates: Sequence[TemplateSpec],
    ) -> CustomNoteType:
        """Create a note type; raises InvalidNoteType or TooManyNoteTypes."""
        note_type = new_note_type(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            name=name,
            fields=fields,
            templates=templates,
            created_at=self._clock.now(),
        )
        if await self._note_types.count_for_owner(owner_id) >= MAX_NOTE_TYPES_PER_OWNER:
            raise TooManyNoteTypes(owner_id)
        await self._note_types.add(note_type)
        return note_type

    async def list_types(self, *, owner_id: str) -> list[CustomNoteType]:
        return await self._note_types.list_for_owner(owner_id)

    async def get(self, *, owner_id: str, note_type_id: str) -> CustomNoteType:
        note_type = await self._note_types.get(note_type_id)
        if note_type is None or note_type.owner_id != owner_id:
            raise NoteTypeNotFound(note_type_id)
        return note_type

    async def update(
        self,
        *,
        owner_id: str,
        note_type_id: str,
        name: str | None = None,
        fields: Sequence[str] | None = None,
        renamed: Mapping[str, str] | None = None,
        templates: Sequence[TemplateSpec] | None = None,
    ) -> CustomNoteType:
        """Edit a note type and re-derive the cards of its notes.

        Every note is checked against the edited type before anything is
        written, so an edit that would leave a note without a card changes
        nothing (InvalidNoteType).
        """
        current = await self._lock(owner_id, note_type_id)
        edited, sources = edit_note_type(
            current, name=name, fields=fields, renamed=renamed, templates=templates
        )
        if fields is None and templates is None:  # a rename leaves every card as it is
            await self._note_types.update(edited)
            return edited
        listed = await self._note_repo.list_for_note_type(note_type_id)
        plan: list[tuple[Note, list[int]]] = []
        for note in await self._note_repo.lock_many([n.id for n in listed]):
            moved = note.with_fields(remap_fields(note.fields, sources))
            try:
                plan.append((moved, edited.card_ordinals(moved.fields)))
            except InvalidNote as exc:
                raise InvalidNoteType(
                    "the edit would leave a note of this type without any card"
                ) from exc
        await self._note_types.update(edited)
        for moved, wanted in plan:
            await self._library.rederive(moved, wanted)
        return edited

    async def delete(self, *, owner_id: str, note_type_id: str) -> None:
        """Delete an unused note type; raises NoteTypeInUse while notes use it."""
        await self._lock(owner_id, note_type_id)
        if await self._note_repo.count_for_note_type(note_type_id):
            raise NoteTypeInUse(note_type_id)
        await self._note_types.delete(note_type_id)

    async def create_note(
        self,
        *,
        owner_id: str,
        deck_id: str,
        note_type: NoteType,
        fields: Mapping[str, str],
        tags: Iterable[str] = (),
        note_type_id: str | None = None,
        occlusion: Occlusion | None = None,
    ) -> tuple[Note, list[Card]]:
        """Create a note of any type; raises InvalidNote, NoteTypeNotFound or MediaNotFound."""
        custom = None
        if note_type_id is not None:
            if note_type is not NoteType.CUSTOM:
                raise InvalidNote("only a custom note has a note type id")
            custom = await self._lock(owner_id, note_type_id)
        if occlusion is not None:
            await self._check_image(owner_id, occlusion.image_id)
        return await self._notes.create_note(
            owner_id=owner_id,
            deck_id=deck_id,
            note_type=note_type,
            fields=fields,
            tags=tags,
            custom=custom,
            occlusion=occlusion,
        )

    async def update_note(
        self,
        *,
        owner_id: str,
        note_id: str,
        fields: Mapping[str, str] | None = None,
        tags: Iterable[str] | None = None,
        occlusion: Occlusion | None = None,
    ) -> tuple[Note, list[Card]]:
        """Edit a note of any type (LibraryService.update_note); new masks re-derive cards."""
        note = await self._notes.get_note(owner_id=owner_id, note_id=note_id)
        custom = None
        if note.note_type_id is not None:
            custom = await self._lock(owner_id, note.note_type_id)
        if occlusion is not None:
            if note.note_type is not NoteType.IMAGE_OCCLUSION:
                raise InvalidNote("only an image occlusion note has an image and masks")
            if note.occlusion is None or occlusion.image_id != note.occlusion.image_id:
                await self._check_image(owner_id, occlusion.image_id)
        return await self._library.update_note(
            owner_id=owner_id,
            note_id=note_id,
            fields=fields,
            tags=tags,
            custom=custom,
            occlusion=occlusion,
        )

    async def _lock(self, owner_id: str, note_type_id: str) -> CustomNoteType:
        note_type = await self._note_types.lock(note_type_id)
        if note_type is None or note_type.owner_id != owner_id:
            raise NoteTypeNotFound(note_type_id)
        return note_type

    async def _check_image(self, owner_id: str, media_id: str) -> None:
        asset = await self._media.get(media_id)
        if asset is None or asset.owner_id != owner_id:
            raise MediaNotFound(media_id)
        if not asset.content_type.startswith("image/"):
            raise InvalidNote("an image occlusion note needs an image, not another media type")
