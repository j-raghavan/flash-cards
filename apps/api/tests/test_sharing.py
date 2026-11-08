# SPDX-License-Identifier: AGPL-3.0-only
"""Deck share links: management, preview and clone (in-memory)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.api.container import Container, build_container
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.invites import hash_token
from flashcards.domain.notes import NoteType
from flashcards.domain.sharing import (
    CloneNotAllowed,
    CloneTooLarge,
    ShareLink,
    ShareNotFound,
)

ALICE, BOB = "alice", "bob"


async def _shared_tree(c: Container) -> tuple[str, str]:
    """Alice's 'Biology' deck with a 'Cells' subdeck; returns (root_id, child_id)."""
    root = await c.decks.create(owner_id=ALICE, name="Biology")
    child = await c.decks.create(owner_id=ALICE, name="Cells", parent_id=root.id)
    await c.notes.create_note(
        owner_id=ALICE, deck_id=root.id, note_type=NoteType.BASIC,
        fields={"front": "What is life?", "back": "Hard to define"},
    )
    await c.notes.create_note(
        owner_id=ALICE, deck_id=child.id, note_type=NoteType.CLOZE,
        fields={"text": "The {{c1::nucleus}} holds {{c2::DNA}}"},
    )
    return root.id, child.id


def test_revoking_keeps_the_first_revocation_time() -> None:
    t0 = datetime(2026, 11, 8, tzinfo=UTC)
    link = ShareLink(
        id="s", owner_id=ALICE, deck_id="d", token_hash="h", allow_clone=False, created_at=t0
    )
    once = link.revoked(t0 + timedelta(hours=1))
    assert once.revoked(t0 + timedelta(hours=2)).revoked_at == t0 + timedelta(hours=1)
    assert not once.is_active and link.is_active


async def test_create_stores_only_the_token_hash() -> None:
    c = build_container()
    root, _child = await _shared_tree(c)

    created = await c.sharing.create(owner_id=ALICE, deck_id=root, allow_clone=True)

    assert created.link.token_hash == hash_token(created.raw_token)
    assert created.raw_token not in created.link.token_hash
    links = await c.sharing.list_for_deck(owner_id=ALICE, deck_id=root)
    assert [link.id for link in links] == [created.link.id]


async def test_only_the_owner_can_share_or_list() -> None:
    c = build_container()
    root, _child = await _shared_tree(c)
    with pytest.raises(DeckNotFound):
        await c.sharing.create(owner_id=BOB, deck_id=root, allow_clone=False)
    with pytest.raises(DeckNotFound):
        await c.sharing.list_for_deck(owner_id=BOB, deck_id=root)


async def test_preview_shows_the_tree_and_notes_without_the_owner() -> None:
    c = build_container()
    root, _child = await _shared_tree(c)
    token = (await c.sharing.create(owner_id=ALICE, deck_id=root, allow_clone=False)).raw_token

    preview = await c.sharing.preview(token=token)

    assert preview.name == "Biology" and preview.allow_clone is False
    assert [node.path for node in preview.decks] == [("Biology",), ("Biology", "Cells")]
    assert preview.note_count == 2
    by_path = {note.deck_path: note for note in preview.notes}
    assert by_path[("Biology", "Cells")].note_type is NoteType.CLOZE


async def test_preview_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("flashcards.application.sharing_service.MAX_PREVIEW_NOTES", 1)
    c = build_container()
    root, _child = await _shared_tree(c)
    token = (await c.sharing.create(owner_id=ALICE, deck_id=root, allow_clone=False)).raw_token
    preview = await c.sharing.preview(token=token)
    assert len(preview.notes) == 1 and preview.note_count == 2


async def test_unknown_and_revoked_tokens_are_not_found() -> None:
    c = build_container()
    root, _child = await _shared_tree(c)
    created = await c.sharing.create(owner_id=ALICE, deck_id=root, allow_clone=True)
    with pytest.raises(ShareNotFound):
        await c.sharing.preview(token="not-a-real-token")

    revoked = await c.sharing.revoke(owner_id=ALICE, link_id=created.link.id)

    assert revoked.revoked_at is not None
    with pytest.raises(ShareNotFound):
        await c.sharing.preview(token=created.raw_token)
    with pytest.raises(ShareNotFound):
        await c.sharing.clone(owner_id=BOB, token=created.raw_token)


async def test_managing_a_link_is_owner_scoped() -> None:
    c = build_container()
    root, _child = await _shared_tree(c)
    link_id = (await c.sharing.create(owner_id=ALICE, deck_id=root, allow_clone=False)).link.id
    with pytest.raises(ShareNotFound):
        await c.sharing.set_allow_clone(owner_id=BOB, link_id=link_id, allow=True)
    with pytest.raises(ShareNotFound):
        await c.sharing.revoke(owner_id=BOB, link_id=link_id)
    with pytest.raises(ShareNotFound):
        await c.sharing.revoke(owner_id=ALICE, link_id="missing")


async def test_clone_requires_the_toggle() -> None:
    c = build_container()
    root, _child = await _shared_tree(c)
    created = await c.sharing.create(owner_id=ALICE, deck_id=root, allow_clone=False)
    with pytest.raises(CloneNotAllowed):
        await c.sharing.clone(owner_id=BOB, token=created.raw_token)

    await c.sharing.set_allow_clone(owner_id=ALICE, link_id=created.link.id, allow=True)
    clone = await c.sharing.clone(owner_id=BOB, token=created.raw_token)
    assert clone.owner_id == BOB


async def test_clone_copies_content_but_not_history() -> None:
    c = build_container()
    root, child = await _shared_tree(c)
    token = (await c.sharing.create(owner_id=ALICE, deck_id=root, allow_clone=True)).raw_token
    # Alice has studied her cards; a clone must not carry that over.
    alice_card = (await c.notes.list_cards(owner_id=ALICE, deck_id=root))[0]
    await c.notes.suspend_card(owner_id=ALICE, card_id=alice_card.id)

    clone = await c.sharing.clone(owner_id=BOB, token=token)

    bob_decks = await c.decks.list_decks(BOB)
    assert sorted(d.name for d in bob_decks) == ["Biology", "Cells"]
    clone_child = next(d for d in bob_decks if d.name == "Cells")
    assert clone.parent_id is None and clone_child.parent_id == clone.id
    root_cards = await c.notes.list_cards(owner_id=BOB, deck_id=clone.id)
    child_cards = await c.notes.list_cards(owner_id=BOB, deck_id=clone_child.id)
    assert len(root_cards) == 1 and len(child_cards) == 2  # cloze cards re-derived
    assert not any(card.is_suspended for card in root_cards)
    assert await c.reviews.due_queue(owner_id=BOB, deck_id=clone.id, limit=10)  # all new
    # The original is untouched.
    assert len(await c.notes.list_cards(owner_id=ALICE, deck_id=child)) == 2


async def test_clone_refuses_oversized_decks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("flashcards.application.sharing_service.MAX_CLONE_NOTES", 1)
    c = build_container()
    root, _child = await _shared_tree(c)
    token = (await c.sharing.create(owner_id=ALICE, deck_id=root, allow_clone=True)).raw_token
    with pytest.raises(CloneTooLarge):
        await c.sharing.clone(owner_id=BOB, token=token)
    assert await c.decks.list_decks(BOB) == []


async def test_sharing_a_subdeck_shares_only_that_subtree() -> None:
    c = build_container()
    _root, child = await _shared_tree(c)
    token = (await c.sharing.create(owner_id=ALICE, deck_id=child, allow_clone=True)).raw_token
    preview = await c.sharing.preview(token=token)
    assert [node.path for node in preview.decks] == [("Cells",)]
    clone = await c.sharing.clone(owner_id=BOB, token=token)
    assert clone.name == "Cells" and clone.parent_id is None


async def test_deleting_the_deck_deletes_its_links() -> None:
    c = build_container()
    root, child = await _shared_tree(c)
    tokens = [
        (await c.sharing.create(owner_id=ALICE, deck_id=deck, allow_clone=False)).raw_token
        for deck in (root, child)
    ]

    await c.library.delete_deck(owner_id=ALICE, deck_id=root)

    for token in tokens:
        with pytest.raises(ShareNotFound):
            await c.sharing.preview(token=token)
