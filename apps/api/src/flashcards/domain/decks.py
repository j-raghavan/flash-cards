# SPDX-License-Identifier: AGPL-3.0-only
"""Deck entity and rules: nested, per-owner decks (F-DECK-1)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

MAX_DECK_NAME = 200


class DeckError(Exception):
    """Base class for deck failures."""


class InvalidDeckName(DeckError):
    pass


class DeckNotFound(DeckError):
    """Raised for a deck that does not exist or is not the caller's.

    Not-found and not-owned are the same error so the API never reveals that a
    deck exists but belongs to someone else.
    """


class DeckCycle(DeckError):
    """Raised when a move would put a deck under itself or a descendant."""


def validate_deck_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > MAX_DECK_NAME:
        raise InvalidDeckName(name)
    return cleaned


@dataclass(frozen=True, slots=True)
class Deck:
    id: str
    owner_id: str
    name: str
    parent_id: str | None
    created_at: datetime

    def renamed(self, name: str) -> Deck:
        return replace(self, name=validate_deck_name(name))

    def moved(self, parent_id: str | None) -> Deck:
        return replace(self, parent_id=parent_id)
