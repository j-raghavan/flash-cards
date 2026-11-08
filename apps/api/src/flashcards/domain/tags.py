# SPDX-License-Identifier: AGPL-3.0-only
"""Note tags (F-CARD-6, ADR 0011).

Tags work as in Anki: no whitespace, `::` separates levels, compared without
regard to case. A note keeps them sorted and without duplicates.
"""

from __future__ import annotations

from collections.abc import Iterable

MAX_TAG_LENGTH = 64
MAX_TAGS_PER_NOTE = 32
LEVEL_SEPARATOR = "::"

Tags = tuple[str, ...]


class InvalidTags(ValueError):
    """A tag is empty, too long or contains whitespace, or there are too many."""


def normalize_tags(raw: Iterable[str]) -> Tags:
    """Validated, de-duplicated (ignoring case) and sorted tags; raises InvalidTags."""
    tags = _unique(tag.strip() for tag in raw)
    for tag in tags:
        if not _is_valid(tag):
            raise InvalidTags(f"invalid tag: {tag!r}")
    if len(tags) > MAX_TAGS_PER_NOTE:
        raise InvalidTags(f"a note can have at most {MAX_TAGS_PER_NOTE} tags")
    return _sorted(tags)


def sanitize_tags(raw: Iterable[str]) -> Tags:
    """Like normalize_tags, but drops invalid tags and extras (used for imports)."""
    valid = [tag for tag in _unique(t.strip() for t in raw) if _is_valid(tag)]
    return _sorted(valid[:MAX_TAGS_PER_NOTE])


def merge_tags(tags: Tags, added: Iterable[str]) -> Tags:
    return normalize_tags([*tags, *added])


def remove_tags(tags: Tags, removed: Iterable[str]) -> Tags:
    gone = {tag.strip().casefold() for tag in removed}
    return tuple(tag for tag in tags if tag.casefold() not in gone)


def has_tag(tags: Tags, wanted: str) -> bool:
    """Whether any tag is `wanted` or one of its descendants (`a` matches `a::b`)."""
    target = wanted.strip().casefold()
    return any(
        tag.casefold() == target or tag.casefold().startswith(target + LEVEL_SEPARATOR)
        for tag in tags
    )


def _is_valid(tag: str) -> bool:
    return 0 < len(tag) <= MAX_TAG_LENGTH and not any(ch.isspace() for ch in tag)


def _unique(tags: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for tag in tags:
        if tag.casefold() not in seen:
            seen.add(tag.casefold())
            unique.append(tag)
    return unique


def _sorted(tags: Iterable[str]) -> Tags:
    return tuple(sorted(tags, key=lambda tag: (tag.casefold(), tag)))
