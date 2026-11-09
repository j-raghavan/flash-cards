# SPDX-License-Identifier: AGPL-3.0-only
"""Request and response models for custom note types and image occlusion (ADR 0013).

Sizes are bounded here so an oversized request is refused before it reaches the
domain, which still validates everything (references, bounds, uniqueness).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from flashcards.domain.note_types import (
    MAX_NOTE_TYPE_NAME_LEN,
    MAX_TEMPLATE_LEN,
    MAX_TEMPLATE_NAME_LEN,
    MAX_TEMPLATE_ORDINAL,
    MAX_TEMPLATES,
    CardTemplate,
    CustomNoteType,
    TemplateSpec,
)
from flashcards.domain.notes import (
    MAX_FIELD_NAME_LEN,
    MAX_FIELDS,
    MAX_MASK_ID,
    MAX_MASKS,
    MAX_MEDIA_ID_LEN,
    Mask,
    Occlusion,
)

FieldName = Annotated[str, StringConstraints(max_length=MAX_FIELD_NAME_LEN)]
Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
PositiveUnit = Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]


class MaskModel(BaseModel):
    """A rectangle in coordinates normalized to the image (0..1); `id` is the card ordinal."""

    id: int = Field(ge=1, le=MAX_MASK_ID)
    left: Unit
    top: Unit
    width: PositiveUnit
    height: PositiveUnit


class OcclusionModel(BaseModel):
    image_id: str = Field(min_length=1, max_length=MAX_MEDIA_ID_LEN)
    masks: list[MaskModel] = Field(min_length=1, max_length=MAX_MASKS)

    def to_domain(self) -> Occlusion:
        return Occlusion(
            image_id=self.image_id,
            masks=tuple(
                Mask(id=m.id, left=m.left, top=m.top, width=m.width, height=m.height)
                for m in self.masks
            ),
        )

    @classmethod
    def from_domain(cls, occlusion: Occlusion | None) -> OcclusionModel | None:
        if occlusion is None:
            return None
        return cls(
            image_id=occlusion.image_id,
            masks=[
                MaskModel(id=m.id, left=m.left, top=m.top, width=m.width, height=m.height)
                for m in occlusion.masks
            ],
        )


class TemplateIn(BaseModel):
    """A card template; give `ordinal` to keep an existing template and its cards."""

    ordinal: int | None = Field(default=None, ge=0, le=MAX_TEMPLATE_ORDINAL)
    name: str = Field(max_length=MAX_TEMPLATE_NAME_LEN)
    front: str = Field(max_length=MAX_TEMPLATE_LEN)
    back: str = Field(max_length=MAX_TEMPLATE_LEN)

    def to_domain(self) -> TemplateSpec:
        return TemplateSpec(name=self.name, front=self.front, back=self.back, ordinal=self.ordinal)


class NoteTypeCreateRequest(BaseModel):
    name: str = Field(max_length=MAX_NOTE_TYPE_NAME_LEN)
    fields: list[FieldName] = Field(max_length=MAX_FIELDS)
    templates: list[TemplateIn] = Field(max_length=MAX_TEMPLATES)


class NoteTypeUpdateRequest(BaseModel):
    """Any of these; omitted parts stay. `renamed` maps old field names to new ones."""

    name: str | None = Field(default=None, max_length=MAX_NOTE_TYPE_NAME_LEN)
    fields: list[FieldName] | None = Field(default=None, max_length=MAX_FIELDS)
    renamed: dict[FieldName, FieldName] = Field(default_factory=dict, max_length=MAX_FIELDS)
    templates: list[TemplateIn] | None = Field(default=None, max_length=MAX_TEMPLATES)


class CardTemplateOut(BaseModel):
    ordinal: int
    name: str
    front: str
    back: str

    @classmethod
    def from_template(cls, template: CardTemplate) -> CardTemplateOut:
        return cls(
            ordinal=template.ordinal, name=template.name, front=template.front, back=template.back
        )


class NoteTypeOut(BaseModel):
    id: str
    name: str
    fields: list[str]
    templates: list[CardTemplateOut]
    created_at: datetime

    @classmethod
    def from_note_type(cls, note_type: CustomNoteType) -> NoteTypeOut:
        return cls(
            id=note_type.id,
            name=note_type.name,
            fields=list(note_type.fields),
            templates=[CardTemplateOut.from_template(t) for t in note_type.templates],
            created_at=note_type.created_at,
        )
