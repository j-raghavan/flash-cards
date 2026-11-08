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

MAX_PREVIEW_NOTES = 200
MAX_CLONE_NOTES = 20_000


class ShareNotFound(Exception):
    """A share link that does not exist, was revoked, or is not the caller's."""


class CloneNotAllowed(Exception):
    """The share link does not allow cloning."""


class CloneTooLarge(Exception):
    """The shared deck has more notes than a clone may copy."""


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
    """One deck of the shared tree; `path` is its name path from the shared root."""

    path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SharedNote:
    deck_path: tuple[str, ...]
    note_type: NoteType
    fields: Fields


@dataclass(frozen=True, slots=True)
class SharedDeckPreview:
    name: str
    decks: tuple[SharedDeckNode, ...]
    notes: tuple[SharedNote, ...]  # at most MAX_PREVIEW_NOTES
    note_count: int
    allow_clone: bool
