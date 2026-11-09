# SPDX-License-Identifier: AGPL-3.0-only
"""Deck sharing use cases: create, manage, preview and clone (F-SHARE-1..3, ADR 0009).

Link management is owner-scoped. Preview and clone are open to any signed-in
account holding an active token; neither reveals who owns the deck. A clone
re-creates decks and notes for the caller, so cards are derived again and no
schedule or review history is copied. Custom note types and image occlusion
images the notes use are copied too, so the clone owns everything it needs
(ADR 0013). Both are bounded: a preview is a small
sample, and a clone is checked against its caps and the caller's daily allowance
before anything is written.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, replace

from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.domain.decks import Deck, DeckNotFound
from flashcards.domain.invites import hash_token
from flashcards.domain.media import MediaAsset
from flashcards.domain.note_types import MAX_NOTE_TYPES_PER_OWNER, CustomNoteType, TooManyNoteTypes
from flashcards.domain.notes import Note, card_ordinals
from flashcards.domain.ports import (
    CardRepository,
    Clock,
    MediaRepository,
    NoteRepository,
    NoteTypeRepository,
    QuotaRepository,
    ShareLinkRepository,
    UserRepository,
)
from flashcards.domain.quotas import clone_period
from flashcards.domain.sharing import (
    MAX_CLONE_CARDS,
    MAX_CLONE_DECKS,
    MAX_CLONE_MEDIA_BYTES,
    MAX_CLONE_NOTES,
    MAX_PREVIEW_DECKS,
    MAX_PREVIEW_NOTES,
    CloneLimitReached,
    CloneNotAllowed,
    CloneTooLarge,
    SharedDeckNode,
    SharedDeckPreview,
    SharedNote,
    ShareLink,
    ShareNotFound,
    trim_fields,
)
from flashcards.domain.users import UserStatus

TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class CreatedShareLink:
    link: ShareLink
    raw_token: str  # returned once; only its hash is stored


@dataclass(frozen=True, slots=True)
class _TreeDeck:
    deck: Deck
    depth: int


class SharingService:
    def __init__(
        self,
        decks: DeckService,
        notes: NoteService,
        note_repo: NoteRepository,
        card_repo: CardRepository,
        links: ShareLinkRepository,
        users: UserRepository,
        clone_quota: QuotaRepository,
        clock: Clock,
        note_types: NoteTypeRepository,
        media: MediaRepository,
    ) -> None:
        self._note_types = note_types
        self._media = media
        self._decks = decks
        self._notes = notes
        self._note_repo = note_repo
        self._card_repo = card_repo
        self._links = links
        self._users = users
        self._clone_quota = clone_quota  # its limit is the daily allowance, in cards
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
        link = await self._owned(owner_id, link_id)
        if not link.is_active:
            raise ShareNotFound(link_id)  # a revoked link cannot be changed
        updated = link.with_allow_clone(allow)
        await self._links.update(updated)
        return updated

    async def revoke(self, *, owner_id: str, link_id: str) -> ShareLink:
        updated = (await self._owned(owner_id, link_id)).revoked(self._clock.now())
        await self._links.update(updated)
        return updated

    async def preview(self, *, token: str) -> SharedDeckPreview:
        link = await self._active(token)
        tree = await self._tree(link)
        shown = tree[:MAX_PREVIEW_DECKS]
        note_count = sum([await self._note_repo.count_for_deck(node.deck.id) for node in tree])
        notes: list[SharedNote] = []
        for index, node in enumerate(shown):
            remaining = MAX_PREVIEW_NOTES - len(notes)
            if remaining <= 0:
                break
            for note in await self._note_repo.list_for_deck(node.deck.id, limit=remaining):
                notes.append(
                    SharedNote(
                        deck_index=index, note_type=note.note_type, fields=trim_fields(note.fields)
                    )
                )
        return SharedDeckPreview(
            name=tree[0].deck.name,
            decks=tuple(SharedDeckNode(name=node.deck.name, depth=node.depth) for node in shown),
            deck_count=len(tree),
            notes=tuple(notes),
            note_count=note_count,
            allow_clone=link.allow_clone,
        )

    async def clone(self, *, owner_id: str, token: str) -> Deck:
        link = await self._active(token)
        if not link.allow_clone:
            raise CloneNotAllowed(link.id)
        tree = await self._tree(link)
        # Bound the work by counting before loading anything heavy.
        if len(tree) > MAX_CLONE_DECKS:
            raise CloneTooLarge(link.id)
        if sum([await self._note_repo.count_for_deck(n.deck.id) for n in tree]) > MAX_CLONE_NOTES:
            raise CloneTooLarge(link.id)
        notes_by_deck: dict[str, list[Note]] = {
            node.deck.id: await self._note_repo.list_for_deck(node.deck.id) for node in tree
        }
        all_notes = [note for notes in notes_by_deck.values() for note in notes]
        source_types = {t.id: t for t in await self._note_types.list_for_owner(link.owner_id)}
        used_ids = dict.fromkeys(n.note_type_id for n in all_notes if n.note_type_id is not None)
        used_types = [source_types[type_id] for type_id in used_ids]
        # Validate every note and count the cards it will make before writing, so a
        # clone either happens in full or not at all, in every storage mode.
        card_total = sum(
            len(
                card_ordinals(
                    note.note_type,
                    note.fields,
                    custom=source_types[note.note_type_id] if note.note_type_id else None,
                    occlusion=note.occlusion,
                )
            )
            for note in all_notes
        )
        if card_total > MAX_CLONE_CARDS:
            raise CloneTooLarge(link.id)
        images = await self._images(link, all_notes)
        owned_types = await self._note_types.count_for_owner(owner_id)
        if used_types and owned_types + len(used_types) > MAX_NOTE_TYPES_PER_OWNER:
            raise TooManyNoteTypes(owner_id)
        period = clone_period(self._clock.now())
        if not await self._clone_quota.try_reserve_pages(owner_id, card_total, period):
            raise CloneLimitReached(owner_id)
        now = self._clock.now()
        type_copies: dict[str, CustomNoteType] = {}
        for source_type in used_types:
            copy = replace(source_type, id=str(uuid.uuid4()), owner_id=owner_id, created_at=now)
            await self._note_types.add(copy)
            type_copies[source_type.id] = copy
        image_copies: dict[str, str] = {}
        for asset in images:
            copied = replace(asset, id=str(uuid.uuid4()), owner_id=owner_id, created_at=now)
            await self._media.add(copied)
            image_copies[asset.id] = copied.id
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
                occlusion = note.occlusion
                if occlusion is not None:
                    occlusion = replace(occlusion, image_id=image_copies[occlusion.image_id])
                await self._notes.create_note(
                    owner_id=owner_id,
                    deck_id=created.id,
                    note_type=note.note_type,
                    fields=dict(note.fields),
                    tags=note.tags,
                    custom=type_copies[note.note_type_id] if note.note_type_id else None,
                    occlusion=occlusion,
                )
        return await self._decks.get(owner_id=owner_id, deck_id=new_ids[link.deck_id])

    async def _images(self, link: ShareLink, notes: list[Note]) -> list[MediaAsset]:
        """The distinct occlusion images the clone copies, within the byte cap."""
        image_ids = dict.fromkeys(n.occlusion.image_id for n in notes if n.occlusion is not None)
        images: list[MediaAsset] = []
        total = 0
        for image_id in image_ids:
            asset = await self._media.get(image_id)
            # Checked when the note was saved, and media outlives the owner's notes.
            if asset is None or asset.owner_id != link.owner_id:  # pragma: no cover
                raise ShareNotFound(link.id)
            total += len(asset.data)
            if total > MAX_CLONE_MEDIA_BYTES:
                raise CloneTooLarge(link.id)
            images.append(asset)
        return images

    async def _owned(self, owner_id: str, link_id: str) -> ShareLink:
        link = await self._links.get(link_id)
        if link is None or link.owner_id != owner_id:
            raise ShareNotFound(link_id)
        return link

    async def _active(self, token: str) -> ShareLink:
        link = await self._links.get_by_token_hash(hash_token(token))
        if link is None or not link.is_active:
            raise ShareNotFound("token")
        owner = await self._users.get(link.owner_id)
        # A suspended (or deleted) owner's links stop serving.
        if owner is None or owner.status() is UserStatus.SUSPENDED:
            raise ShareNotFound("token")
        return link

    async def _tree(self, link: ShareLink) -> list[_TreeDeck]:
        """The shared deck and its descendants in pre-order, parents first."""
        try:
            ids = await self._decks.subtree_ids(owner_id=link.owner_id, deck_id=link.deck_id)
        except DeckNotFound as exc:  # pragma: no cover - links are deleted with their deck
            raise ShareNotFound(link.id) from exc
        depths: dict[str, int] = {}
        tree: list[_TreeDeck] = []
        for deck_id in ids:
            deck = await self._decks.get(owner_id=link.owner_id, deck_id=deck_id)
            depth = 0 if deck_id == link.deck_id else depths[deck.parent_id or ""] + 1
            depths[deck_id] = depth
            tree.append(_TreeDeck(deck=deck, depth=depth))
        return tree
