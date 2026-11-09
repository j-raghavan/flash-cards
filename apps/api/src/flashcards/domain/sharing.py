# SPDX-License-Identifier: AGPL-3.0-only
"""Deck share links (F-SHARE-1..3, ADR 0009).

A link grants read-only access to one deck subtree through an unguessable token,
stored only as a hash. The owner can revoke it or allow recipients to clone the
deck; a clone copies content, never review history.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from flashcards.domain.notes import Fields, NoteType

# A preview is a bounded sample: a few hundred decks and notes, field text trimmed.
MAX_PREVIEW_DECKS = 200
MAX_PREVIEW_NOTES = 200
MAX_PREVIEW_FIELD_CHARS = 500
# A clone is bounded before anything is written. Cards are capped as well as notes,
# since one cloze note can yield many cards.
MAX_CLONE_DECKS = 500
MAX_CLONE_NOTES = 20_000
MAX_CLONE_CARDS = 20_000
# Image occlusion images are copied to the recipient (ADR 0013), so they are capped too.
MAX_CLONE_MEDIA_BYTES = 100 * 1024 * 1024
# Each started MiB of copied images costs one card's worth of the daily clone allowance.
CLONE_UNIT_BYTES = 1024 * 1024


class ShareNotFound(Exception):
    """A share link that does not exist, was revoked, or is not the caller's."""


class CloneNotAllowed(Exception):
    """The share link does not allow cloning."""


class CloneTooLarge(Exception):
    """The shared deck has more decks, notes or cards than a clone may copy."""


class CloneLimitReached(Exception):
    """The caller has used today's clone allowance."""


@dataclass(frozen=True, slots=True)
class ShareLink:
    id: str
    owner_id: str
    deck_id: str
    token_hash: str
    allow_clone: bool
    created_at: datetime
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def revoked(self, now: datetime) -> ShareLink:
        # Revoking twice keeps the first revocation time.
        return self if self.revoked_at is not None else replace(self, revoked_at=now)

    def with_allow_clone(self, allow: bool) -> ShareLink:
        return replace(self, allow_clone=allow)


@dataclass(frozen=True, slots=True)
class SharedDeckNode:
    """One deck of the shared tree, in pre-order; depth 0 is the shared deck itself."""

    name: str
    depth: int


@dataclass(frozen=True, slots=True)
class SharedNote:
    deck_index: int  # position of its deck in SharedDeckPreview.decks
    note_type: NoteType
    fields: Fields  # each value trimmed to MAX_PREVIEW_FIELD_CHARS


@dataclass(frozen=True, slots=True)
class SharedDeckPreview:
    name: str
    decks: tuple[SharedDeckNode, ...]  # at most MAX_PREVIEW_DECKS
    deck_count: int
    notes: tuple[SharedNote, ...]  # at most MAX_PREVIEW_NOTES
    note_count: int
    allow_clone: bool


def trim_fields(fields: Fields, limit: int = MAX_PREVIEW_FIELD_CHARS) -> Fields:
    """Field values cut to `limit` characters, marked with an ellipsis when cut."""
    return tuple(
        (name, value if len(value) <= limit else value[:limit] + "\u2026")
        for name, value in fields
    )
