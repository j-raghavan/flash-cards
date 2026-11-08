# SPDX-License-Identifier: AGPL-3.0-only
"""Deck sharing use cases: create, manage, preview and clone (F-SHARE-1..3, ADR 0009).

Link management is owner-scoped. Preview and clone are open to any signed-in
account holding an active token; neither reveals who owns the deck. A clone
re-creates decks and notes for the caller, so cards are derived again and no
schedule or review history is copied.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.domain.decks import Deck, DeckNotFound
from flashcards.domain.invites import hash_token
from flashcards.domain.notes import Note
from flashcards.domain.ports import (
    Clock,
    NoteRepository,
    ShareLinkRepository,
)
from flashcards.domain.sharing import (
    MAX_CLONE_NOTES,
    MAX_PREVIEW_NOTES,
    CloneNotAllowed,
    CloneTooLarge,
    SharedDeckNode,
    SharedDeckPreview,
    SharedNote,
    ShareLink,
    ShareNotFound,
)

TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class CreatedShareLink:
    link: ShareLink
    raw_token: str  # returned once; only its hash is stored


@dataclass(frozen=True, slots=True)
class _TreeDeck:
    deck: Deck
    path: tuple[str, ...]


class SharingService:
    def __init__(
        self,
        decks: DeckService,
        notes: NoteService,
        note_repo: NoteRepository,
        links: ShareLinkRepository,
        clock: Clock,
    ) -> None:
        self._decks = decks
        self._notes = notes
        self._note_repo = note_repo
        self._links = links
        self._clock = clock

    async def create(
        self, *, owner_id: str, deck_id: str, allow_clone: bool
    ) -> CreatedShareLink:
        # Raises DeckNotFound if the deck is missing or not the caller's.
        await self._decks.get(owner_id=owner_id, deck_id=deck_id)
        raw_token = secrets.token_urlsafe(TOKEN_BYTES)
        link = ShareLink(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            deck_id=deck_id,
            token_hash=hash_token(raw_token),
            allow_clone=allow_clone,
            created_at=self._clock.now(),
        )
        await self._links.add(link)
        return CreatedShareLink(link=link, raw_token=raw_token)

    async def list_for_deck(self, *, owner_id: str, deck_id: str) -> list[ShareLink]:
        await self._decks.get(owner_id=owner_id, deck_id=deck_id)
        return await self._links.list_for_deck(deck_id)

    async def set_allow_clone(self, *, owner_id: str, link_id: str, allow: bool) -> ShareLink:
        updated = (await self._owned(owner_id, link_id)).with_allow_clone(allow)
        await self._links.update(updated)
        return updated

    async def revoke(self, *, owner_id: str, link_id: str) -> ShareLink:
        updated = (await self._owned(owner_id, link_id)).revoked(self._clock.now())
        await self._links.update(updated)
        return updated

    async def preview(self, *, token: str) -> SharedDeckPreview:
        link = await self._active(token)
        tree = await self._tree(link)
        notes: list[SharedNote] = []
        count = 0
        for node in tree:
            for note in await self._note_repo.list_for_deck(node.deck.id):
                count += 1
                if len(notes) < MAX_PREVIEW_NOTES:
                    shared = SharedNote(
                        deck_path=node.path, note_type=note.note_type, fields=note.fields
                    )
                    notes.append(shared)
        return SharedDeckPreview(
            name=tree[0].deck.name,
            decks=tuple(SharedDeckNode(path=node.path) for node in tree),
            notes=tuple(notes),
            note_count=count,
            allow_clone=link.allow_clone,
        )

    async def clone(self, *, owner_id: str, token: str) -> Deck:
        link = await self._active(token)
        if not link.allow_clone:
            raise CloneNotAllowed(link.id)
        tree = await self._tree(link)
        notes_by_deck: dict[str, list[Note]] = {
            node.deck.id: await self._note_repo.list_for_deck(node.deck.id) for node in tree
        }
        if sum(len(notes) for notes in notes_by_deck.values()) > MAX_CLONE_NOTES:
            raise CloneTooLarge(link.id)
        # Parents come before children in the tree, so each new parent exists first.
        new_ids: dict[str, str] = {}
        for node in tree:
            source = node.deck
            parent_id = None if source.id == link.deck_id else new_ids[source.parent_id or ""]
            created = await self._decks.create(
                owner_id=owner_id, name=source.name, parent_id=parent_id
            )
            new_ids[source.id] = created.id
            for note in notes_by_deck[source.id]:
                await self._notes.create_note(
                    owner_id=owner_id,
                    deck_id=created.id,
                    note_type=note.note_type,
                    fields=dict(note.fields),
                )
        return await self._decks.get(owner_id=owner_id, deck_id=new_ids[link.deck_id])

    async def _owned(self, owner_id: str, link_id: str) -> ShareLink:
        link = await self._links.get(link_id)
        if link is None or link.owner_id != owner_id:
            raise ShareNotFound(link_id)
        return link

    async def _active(self, token: str) -> ShareLink:
        link = await self._links.get_by_token_hash(hash_token(token))
        if link is None or not link.is_active:
            raise ShareNotFound("token")
        return link

    async def _tree(self, link: ShareLink) -> list[_TreeDeck]:
        """The shared deck and its descendants, parents first, with name paths."""
        try:
            ids = await self._decks.subtree_ids(owner_id=link.owner_id, deck_id=link.deck_id)
        except DeckNotFound as exc:  # pragma: no cover - links are deleted with their deck
            raise ShareNotFound(link.id) from exc
        tree: list[_TreeDeck] = []
        paths: dict[str, tuple[str, ...]] = {}
        for deck_id in ids:
            deck = await self._decks.get(owner_id=link.owner_id, deck_id=deck_id)
            parent_path = paths.get(deck.parent_id or "", ()) if deck_id != link.deck_id else ()
            paths[deck_id] = (*parent_path, deck.name)
            tree.append(_TreeDeck(deck=deck, path=paths[deck_id]))
        return tree
