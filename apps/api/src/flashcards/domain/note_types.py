# SPDX-License-Identifier: AGPL-3.0-only
"""User-defined note types: named fields and card templates (F-CARD-4, ADR 0013).

An Anki-compatible subset. A template is text with `{{Field}}` placeholders; the
back may also use `{{FrontSide}}` for the rendered front. Nothing else inside
double braces is allowed (no conditionals, filters or cloze), so every template
the server accepts renders the same way in every client.

Each template has a stable ordinal, which is the ordinal of the cards it makes.
A note gets a card for each template whose front uses at least one non-empty
field, as Anki skips empty fronts; a note that would get no card is refused.

Rendering happens in clients, which treat field values and templates as
untrusted HTML and sanitize the result.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from flashcards.domain.notes import (
    MAX_FIELD_NAME_LEN,
    MAX_FIELDS,
    CardError,
    Fields,
    InvalidNote,
)

MAX_NOTE_TYPES_PER_OWNER = 100
MAX_NOTE_TYPE_NAME_LEN = 100
MAX_TEMPLATES = 16
MAX_TEMPLATE_NAME_LEN = 64
MAX_TEMPLATE_LEN = 10_000
MAX_TEMPLATE_ORDINAL = 9_999
FRONT_SIDE = "FrontSide"
_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
# Characters Anki gives meaning to inside a placeholder, so never part of a name.
_RESERVED_CHARS = frozenset('{}:#/^"')


class InvalidNoteType(CardError):
    """A note type definition that breaks a rule; the message says which."""


class NoteTypeNotFound(CardError):
    """A note type that does not exist or is not the caller's."""


class NoteTypeInUse(CardError):
    """A note type that notes still use, so it cannot be deleted."""


class TooManyNoteTypes(CardError):
    """The owner already has MAX_NOTE_TYPES_PER_OWNER note types."""


@dataclass(frozen=True, slots=True)
class CardTemplate:
    ordinal: int
    name: str
    front: str
    back: str


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    """A requested template; `ordinal` names an existing one to keep, None adds one."""

    name: str
    front: str
    back: str
    ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class CustomNoteType:
    id: str
    owner_id: str
    name: str
    fields: tuple[str, ...]
    templates: tuple[CardTemplate, ...]
    created_at: datetime

    def template(self, ordinal: int) -> CardTemplate | None:
        return next((t for t in self.templates if t.ordinal == ordinal), None)

    def normalize_fields(self, given: Mapping[str, str]) -> Fields:
        """Every field of the type in order; missing ones are empty. Raises InvalidNote."""
        known = set(self.fields)
        if any(name not in known for name in given):
            raise InvalidNote("the note has a field its note type does not define")
        return tuple((name, given.get(name, "")) for name in self.fields)

    def card_ordinals(self, fields: Fields) -> list[int]:
        """The ordinals of the templates whose front uses a non-empty field."""
        values = dict(fields)
        if any(name not in self.fields for name in values):
            raise InvalidNote("the note has a field its note type does not define")
        ordinals = [
            t.ordinal
            for t in self.templates
            if any(values.get(name, "").strip() for name in placeholders(t.front))
        ]
        if not ordinals:
            raise InvalidNote("the note would make no cards: fill in a field a card front uses")
        return sorted(ordinals)

    def with_definition(
        self, *, name: str, fields: tuple[str, ...], templates: tuple[CardTemplate, ...]
    ) -> CustomNoteType:
        return replace(self, name=name, fields=fields, templates=templates)


def placeholders(template: str) -> list[str]:
    """The names inside the template's {{...}} placeholders, in order."""
    return [match.group(1) for match in _PLACEHOLDER.finditer(template)]


def _has_control_chars(text: str) -> bool:
    return any(unicodedata.category(ch) in ("Cc", "Cf") for ch in text)


def _validate_type_name(name: str) -> str:
    cleaned = name.strip()
    if not 0 < len(cleaned) <= MAX_NOTE_TYPE_NAME_LEN or _has_control_chars(cleaned):
        raise InvalidNoteType(
            f"a note type name is 1 to {MAX_NOTE_TYPE_NAME_LEN} printable characters"
        )
    return cleaned


def _validate_field_names(fields: Sequence[str]) -> tuple[str, ...]:
    if not 0 < len(fields) <= MAX_FIELDS:
        raise InvalidNoteType(f"a note type has 1 to {MAX_FIELDS} fields")
    seen: set[str] = set()
    for position, name in enumerate(fields, start=1):
        if (
            not 0 < len(name) <= MAX_FIELD_NAME_LEN
            or name != name.strip()
            or _has_control_chars(name)
            or any(ch in _RESERVED_CHARS for ch in name)
        ):
            raise InvalidNoteType(
                f"field {position}: names are 1 to {MAX_FIELD_NAME_LEN} characters, without "
                'surrounding spaces or any of { } : # / ^ "'
            )
        key = name.casefold()
        if key == FRONT_SIDE.casefold():
            raise InvalidNoteType(f"field {position}: {FRONT_SIDE} is reserved")
        if key in seen:
            raise InvalidNoteType(f"field {position}: field names must be unique")
        seen.add(key)
    return tuple(fields)


def _validate_side(text: str, fields: frozenset[str], *, front: bool, position: int) -> None:
    side = "front" if front else "back"
    if len(text) > MAX_TEMPLATE_LEN:
        raise InvalidNoteType(
            f"template {position}: the {side} is longer than {MAX_TEMPLATE_LEN} characters"
        )
    names = placeholders(text)
    allowed = fields if front else fields | {FRONT_SIDE}
    if any(name not in allowed for name in names):
        raise InvalidNoteType(
            f"template {position}: the {side} uses a placeholder that is not a field"
            + ("" if front else f" or {{{{{FRONT_SIDE}}}}}")
        )
    # A stray brace pair would render literally in one client and not another.
    rest = _PLACEHOLDER.sub("", text)
    if "{{" in rest or "}}" in rest:
        raise InvalidNoteType(f"template {position}: the {side} has an unmatched {{{{ or }}}}")
    if front and not names:
        raise InvalidNoteType(f"template {position}: the front must use at least one field")


def build_templates(
    specs: Sequence[TemplateSpec],
    fields: tuple[str, ...],
    existing: tuple[CardTemplate, ...] = (),
) -> tuple[CardTemplate, ...]:
    """Validate the requested templates and give each a stable ordinal.

    A spec with an ordinal keeps that existing template's ordinal (and so its
    cards); a spec without one gets a fresh ordinal above every current and
    kept template, so it never takes over the cards of another template. An
    ordinal freed by a template removed in an earlier edit (whose cards were
    purged with it) may be given out again.
    """
    if not 0 < len(specs) <= MAX_TEMPLATES:
        raise InvalidNoteType(f"a note type has 1 to {MAX_TEMPLATES} templates")
    known_ordinals = {t.ordinal for t in existing}
    field_set = frozenset(fields)
    names: set[str] = set()
    kept: set[int] = set()
    for position, spec in enumerate(specs, start=1):
        name = spec.name.strip()
        if not 0 < len(name) <= MAX_TEMPLATE_NAME_LEN or _has_control_chars(name):
            raise InvalidNoteType(
                f"template {position}: names are 1 to {MAX_TEMPLATE_NAME_LEN} printable characters"
            )
        if name.casefold() in names:
            raise InvalidNoteType(f"template {position}: template names must be unique")
        names.add(name.casefold())
        if spec.ordinal is not None:
            if spec.ordinal not in known_ordinals or spec.ordinal in kept:
                raise InvalidNoteType(
                    f"template {position}: the ordinal must name a different existing template"
                )
            kept.add(spec.ordinal)
        _validate_side(spec.front, field_set, front=True, position=position)
        _validate_side(spec.back, field_set, front=False, position=position)
    next_ordinal = max(known_ordinals, default=-1) + 1
    templates: list[CardTemplate] = []
    for spec in specs:
        ordinal = spec.ordinal
        if ordinal is None:
            ordinal, next_ordinal = next_ordinal, next_ordinal + 1
        if ordinal > MAX_TEMPLATE_ORDINAL:
            raise InvalidNoteType("this note type has run out of template ordinals")
        templates.append(
            CardTemplate(ordinal=ordinal, name=spec.name.strip(), front=spec.front, back=spec.back)
        )
    return tuple(templates)


def new_note_type(
    *,
    id: str,
    owner_id: str,
    name: str,
    fields: Sequence[str],
    templates: Sequence[TemplateSpec],
    created_at: datetime,
) -> CustomNoteType:
    """A validated note type; raises InvalidNoteType."""
    field_names = _validate_field_names(fields)
    return CustomNoteType(
        id=id,
        owner_id=owner_id,
        name=_validate_type_name(name),
        fields=field_names,
        templates=build_templates(templates, field_names),
        created_at=created_at,
    )


def field_sources(
    old: tuple[str, ...], new: tuple[str, ...], renamed: Mapping[str, str]
) -> dict[str, str | None]:
    """For each new field, the old field whose values it takes (None: starts empty).

    `renamed` maps old names to new ones. A field not renamed keeps its values
    when its name is still in the list; a field renamed away leaves its old name
    free for a new, empty field.
    """
    if any(source not in old for source in renamed):
        raise InvalidNoteType("a renamed field is not a field of this note type")
    targets = list(renamed.values())
    if any(target not in new for target in targets) or len(set(targets)) != len(targets):
        raise InvalidNoteType("each field can be renamed to one distinct new field")
    # Renaming onto a field that keeps its own values would silently drop them.
    if any(target in old and target not in renamed for target in targets):
        raise InvalidNoteType("a field cannot be renamed to another field that is kept")
    by_target = {target: source for source, target in renamed.items()}
    return {
        name: by_target.get(name, name if name in old and name not in renamed else None)
        for name in new
    }


def edit_note_type(
    note_type: CustomNoteType,
    *,
    name: str | None = None,
    fields: Sequence[str] | None = None,
    renamed: Mapping[str, str] | None = None,
    templates: Sequence[TemplateSpec] | None = None,
) -> tuple[CustomNoteType, dict[str, str | None]]:
    """The edited type and where each of its fields takes its values from.

    Omitted parts stay as they are. Templates left unchanged are checked again
    against the new fields, so renaming a field the templates use needs new
    templates in the same edit.
    """
    if fields is None and renamed:
        raise InvalidNoteType("renaming fields needs the new list of fields")
    field_names = note_type.fields if fields is None else _validate_field_names(fields)
    sources = field_sources(note_type.fields, field_names, renamed or {})
    if templates is None:
        specs = [TemplateSpec(t.name, t.front, t.back, t.ordinal) for t in note_type.templates]
    else:
        specs = list(templates)
    edited = note_type.with_definition(
        name=note_type.name if name is None else _validate_type_name(name),
        fields=field_names,
        templates=build_templates(specs, field_names, note_type.templates),
    )
    return edited, sources


def remap_fields(fields: Fields, sources: Mapping[str, str | None]) -> Fields:
    """A note's fields moved onto the edited type's fields."""
    values = dict(fields)
    return tuple(
        (name, values.get(source, "") if source is not None else "")
        for name, source in sources.items()
    )
