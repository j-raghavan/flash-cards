# SPDX-License-Identifier: AGPL-3.0-only
"""Use cases for nested, per-owner decks (F-DECK-1)."""

from __future__ import annotations

import uuid

from flashcards.domain.decks import Deck, DeckCycle, DeckNotFound, validate_deck_name
from flashcards.domain.ports import Clock, DeckRepository


class DeckService:
    def __init__(self, repo: DeckRepository, clock: Clock) -> None:
        self._repo = repo
        self._clock = clock

    async def create(self, *, owner_id: str, name: str, parent_id: str | None = None) -> Deck:
        validated = validate_deck_name(name)
        if parent_id is not None:
            await self._require_owned(owner_id, parent_id)
        deck = Deck(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            name=validated,
            parent_id=parent_id,
            created_at=self._clock.now(),
        )
        await self._repo.add(deck)
        return deck

    async def list_decks(self, owner_id: str) -> list[Deck]:
        return await self._repo.list_for_owner(owner_id)

    async def get(self, *, owner_id: str, deck_id: str) -> Deck:
        return await self._require_owned(owner_id, deck_id)

    async def rename(self, *, owner_id: str, deck_id: str, name: str) -> Deck:
        deck = await self._require_owned(owner_id, deck_id)
        renamed = deck.renamed(name)
        await self._repo.update(renamed)
        return renamed

    async def move(self, *, owner_id: str, deck_id: str, new_parent_id: str | None) -> Deck:
        deck = await self._require_owned(owner_id, deck_id)
        if new_parent_id is not None:
            await self._require_owned(owner_id, new_parent_id)
            await self._guard_no_cycle(deck_id=deck_id, start_parent_id=new_parent_id)
        moved = deck.moved(new_parent_id)
        await self._repo.update(moved)
        return moved

    async def delete(self, *, owner_id: str, deck_id: str) -> list[str]:
        """Delete a deck and all its descendant decks; return the ids removed.

        This removes deck rows only. Cross-aggregate cleanup (the notes, cards,
        schedules and logs inside the decks) is the LibraryService's job.
        """
        subtree = await self.subtree_ids(owner_id=owner_id, deck_id=deck_id)
        for removed_id in subtree:
            await self._repo.delete(removed_id)
        return subtree

    async def subtree_ids(self, *, owner_id: str, deck_id: str) -> list[str]:
        """Return a deck and all its descendant deck ids, after verifying ownership."""
        await self._require_owned(owner_id, deck_id)
        decks = await self._repo.list_for_owner(owner_id)
        return _subtree_ids(deck_id, decks)

    async def _require_owned(self, owner_id: str, deck_id: str) -> Deck:
        deck = await self._repo.get(deck_id)
        if deck is None or deck.owner_id != owner_id:
            raise DeckNotFound(deck_id)
        return deck

    async def _guard_no_cycle(self, *, deck_id: str, start_parent_id: str) -> None:
        current: str | None = start_parent_id
        while current is not None:
            if current == deck_id:
                raise DeckCycle(deck_id)
            node = await self._repo.get(current)
            if node is None:
                return
            current = node.parent_id


def _subtree_ids(root_id: str, decks: list[Deck]) -> list[str]:
    children: dict[str | None, list[str]] = {}
    for deck in decks:
        children.setdefault(deck.parent_id, []).append(deck.id)
    # Pre-order: every parent precedes its children, and siblings keep the order
    # the repository lists them in (oldest first), so copies keep the same order.
    ordered: list[str] = []
    stack = [root_id]
    while stack:
        node_id = stack.pop()
        ordered.append(node_id)
        stack.extend(reversed(children.get(node_id, [])))
    return ordered
