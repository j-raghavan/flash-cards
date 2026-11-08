# SPDX-License-Identifier: AGPL-3.0-only
"""Deck share links: management, preview and clone (in-memory)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryNoteRepository,
    InMemoryQuotaRepository,
    InMemoryShareLinkRepository,
    InMemoryUserRepository,
    SystemClock,
)
from flashcards.api.container import Container, build_container
from flashcards.application.deck_service import DeckService
from flashcards.application.note_service import NoteService
from flashcards.application.sharing_service import SharingService
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.invites import Role, hash_token
from flashcards.domain.notes import InvalidNote, Note, NoteType
from flashcards.domain.scheduling import Rating
from flashcards.domain.sharing import (
    CloneLimitReached,
    CloneNotAllowed,
    CloneTooLarge,
    SharedDeckNode,
    ShareLink,
    ShareNotFound,
    trim_fields,
)
from flashcards.domain.users import User

ALICE, BOB = "alice", "bob"
NOW = datetime(2026, 11, 8, tzinfo=UTC)


def _user(user_id: str) -> User:
    return User(
        id=user_id, email=f"{user_id}@example.com", password_hash="h", role=Role.MEMBER,
        created_at=NOW,
    )


async def _container() -> Container:
    c = build_container()
    await c.user_repo.add(_user(ALICE))
    await c.user_repo.add(_user(BOB))
    return c


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


async def _token(c: Container, deck_id: str, *, allow_clone: bool = True) -> str:
    created = await c.sharing.create(owner_id=ALICE, deck_id=deck_id, allow_clone=allow_clone)
    return created.raw_token


def test_revoking_keeps_the_first_revocation_time() -> None:
    link = ShareLink(
        id="s", owner_id=ALICE, deck_id="d", token_hash="h", allow_clone=False, created_at=NOW
    )
    once = link.revoked(NOW + timedelta(hours=1))
    assert once.revoked(NOW + timedelta(hours=2)).revoked_at == NOW + timedelta(hours=1)
    assert not once.is_active and link.is_active


def test_trim_fields_marks_cut_values() -> None:
    assert trim_fields((("a", "short"), ("b", "x" * 10)), limit=5) == (
        ("a", "short"),
        ("b", "xxxxx…"),
    )


async def test_create_stores_only_the_token_hash() -> None:
    c = await _container()
    root, _child = await _shared_tree(c)

    created = await c.sharing.create(owner_id=ALICE, deck_id=root, allow_clone=True)

    assert created.link.token_hash == hash_token(created.raw_token)
    assert created.raw_token not in created.link.token_hash
    links = await c.sharing.list_for_deck(owner_id=ALICE, deck_id=root)
    assert [link.id for link in links] == [created.link.id]


async def test_only_the_owner_can_share_or_list() -> None:
    c = await _container()
    root, _child = await _shared_tree(c)
    with pytest.raises(DeckNotFound):
        await c.sharing.create(owner_id=BOB, deck_id=root, allow_clone=False)
    with pytest.raises(DeckNotFound):
        await c.sharing.list_for_deck(owner_id=BOB, deck_id=root)


async def test_preview_shows_the_tree_and_notes_without_the_owner() -> None:
    c = await _container()
    root, _child = await _shared_tree(c)

    preview = await c.sharing.preview(token=await _token(c, root, allow_clone=False))

    assert preview.name == "Biology" and preview.allow_clone is False
    assert preview.decks == (SharedDeckNode("Biology", 0), SharedDeckNode("Cells", 1))
    assert preview.deck_count == 2 and preview.note_count == 2
    by_deck = {note.deck_index: note for note in preview.notes}
    assert by_deck[1].note_type is NoteType.CLOZE


async def test_preview_caps_the_decks_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("flashcards.application.sharing_service.MAX_PREVIEW_DECKS", 1)
    c = await _container()
    root, _child = await _shared_tree(c)
    preview = await c.sharing.preview(token=await _token(c, root))
    assert len(preview.decks) == 1 and preview.deck_count == 2
    # Notes come only from the decks shown, but the count covers the whole tree.
    assert {n.deck_index for n in preview.notes} == {0} and preview.note_count == 2


async def test_preview_caps_the_notes_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("flashcards.application.sharing_service.MAX_PREVIEW_NOTES", 1)
    c = await _container()
    root, _child = await _shared_tree(c)
    preview = await c.sharing.preview(token=await _token(c, root))
    # The first deck fills the sample, so the second deck is not even read.
    assert len(preview.decks) == 2
    assert len(preview.notes) == 1 and preview.note_count == 2


async def test_preview_trims_long_fields() -> None:
    c = await _container()
    deck = await c.decks.create(owner_id=ALICE, name="Long")
    await c.notes.create_note(
        owner_id=ALICE, deck_id=deck.id, note_type=NoteType.BASIC,
        fields={"front": "q", "back": "z" * 2_000},
    )
    preview = await c.sharing.preview(token=await _token(c, deck.id))
    back = dict(preview.notes[0].fields)["back"]
    assert len(back) == 501 and back.endswith("…")


async def test_siblings_keep_their_order_in_preview_and_clone() -> None:
    c = await _container()
    root = await c.decks.create(owner_id=ALICE, name="Root")
    for name in ("A", "B", "C"):
        await c.decks.create(owner_id=ALICE, name=name, parent_id=root.id)
    token = await _token(c, root.id)

    preview = await c.sharing.preview(token=token)
    await c.sharing.clone(owner_id=BOB, token=token)

    assert [d.name for d in preview.decks] == ["Root", "A", "B", "C"]
    assert [d.name for d in await c.decks.list_decks(BOB)] == ["Root", "A", "B", "C"]


async def test_unknown_and_revoked_tokens_are_not_found() -> None:
    c = await _container()
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
    with pytest.raises(ShareNotFound):  # a revoked link cannot be changed
        await c.sharing.set_allow_clone(owner_id=ALICE, link_id=created.link.id, allow=False)


async def test_a_suspended_or_deleted_owner_stops_sharing() -> None:
    service, decks, _notes, users = await _rig(clone_allowance=100)
    deck = await decks.create(owner_id=ALICE, name="Deck")
    token = (await service.create(owner_id=ALICE, deck_id=deck.id, allow_clone=True)).raw_token
    await service.preview(token=token)  # serving while the owner is active

    # No suspend API exists yet, so set the stored user directly.
    users._by_id[ALICE] = replace(_user(ALICE), suspended_at=NOW)
    with pytest.raises(ShareNotFound):
        await service.preview(token=token)
    await users.delete(ALICE)
    with pytest.raises(ShareNotFound):
        await service.preview(token=token)


async def test_managing_a_link_is_owner_scoped() -> None:
    c = await _container()
    root, _child = await _shared_tree(c)
    link_id = (await c.sharing.create(owner_id=ALICE, deck_id=root, allow_clone=False)).link.id
    with pytest.raises(ShareNotFound):
        await c.sharing.set_allow_clone(owner_id=BOB, link_id=link_id, allow=True)
    with pytest.raises(ShareNotFound):
        await c.sharing.revoke(owner_id=BOB, link_id=link_id)
    with pytest.raises(ShareNotFound):
        await c.sharing.revoke(owner_id=ALICE, link_id="missing")


async def test_clone_requires_the_toggle() -> None:
    c = await _container()
    root, _child = await _shared_tree(c)
    created = await c.sharing.create(owner_id=ALICE, deck_id=root, allow_clone=False)
    with pytest.raises(CloneNotAllowed):
        await c.sharing.clone(owner_id=BOB, token=created.raw_token)

    await c.sharing.set_allow_clone(owner_id=ALICE, link_id=created.link.id, allow=True)
    clone = await c.sharing.clone(owner_id=BOB, token=created.raw_token)
    assert clone.owner_id == BOB


async def test_clone_copies_content_but_not_history() -> None:
    c = await _container()
    root, child = await _shared_tree(c)
    token = await _token(c, root)
    # Alice has studied and suspended cards; a clone must carry none of that over.
    alice_cards = await c.notes.list_cards(owner_id=ALICE, deck_id=child)
    await c.reviews.submit(owner_id=ALICE, card_id=alice_cards[0].id, rating=Rating.GOOD)
    await c.notes.suspend_card(owner_id=ALICE, card_id=alice_cards[1].id)

    clone = await c.sharing.clone(owner_id=BOB, token=token)

    bob_decks = await c.decks.list_decks(BOB)
    assert [d.name for d in bob_decks] == ["Biology", "Cells"]
    clone_child = bob_decks[1]
    assert clone.parent_id is None and clone_child.parent_id == clone.id
    child_cards = await c.notes.list_cards(owner_id=BOB, deck_id=clone_child.id)
    assert len(child_cards) == 2  # cloze cards re-derived
    assert not any(card.is_suspended for card in child_cards)
    # Every cloned card is brand new: due now, no review history carried over.
    due = await c.reviews.due_queue(owner_id=BOB, deck_id=clone_child.id, limit=10)
    assert {card.id for card in due} == {card.id for card in child_cards}
    stats = await c.reviews.deck_stats(owner_id=BOB, deck_id=clone_child.id)
    assert stats.new == 2 and stats.review == 0 and stats.learning == 0
    # The original is untouched.
    assert len(await c.notes.list_cards(owner_id=ALICE, deck_id=child)) == 2


async def test_clone_keeps_note_tags() -> None:
    c = await _container()
    deck = await c.decks.create(owner_id=ALICE, name="Tagged")
    await c.notes.create_note(
        owner_id=ALICE, deck_id=deck.id, note_type=NoteType.BASIC,
        fields={"front": "q", "back": "a"}, tags=("bio", "exam::1"),
    )
    clone = await c.sharing.clone(owner_id=BOB, token=await _token(c, deck.id))
    cards = await c.notes.list_cards(owner_id=BOB, deck_id=clone.id)
    note = await c.notes.get_note(owner_id=BOB, note_id=cards[0].note_id)
    assert note.tags == ("bio", "exam::1")


async def test_sharing_a_subdeck_shares_only_that_subtree() -> None:
    c = await _container()
    _root, child = await _shared_tree(c)
    token = await _token(c, child)
    preview = await c.sharing.preview(token=token)
    assert preview.decks == (SharedDeckNode("Cells", 0),)
    clone = await c.sharing.clone(owner_id=BOB, token=token)
    assert clone.name == "Cells" and clone.parent_id is None


@pytest.mark.parametrize("cap", ["MAX_CLONE_DECKS", "MAX_CLONE_NOTES", "MAX_CLONE_CARDS"])
async def test_clone_refuses_oversized_decks(cap: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # The tree has 2 decks, 2 notes and 3 cards, so a cap of 1 trips every check.
    monkeypatch.setattr(f"flashcards.application.sharing_service.{cap}", 1)
    c = await _container()
    root, _child = await _shared_tree(c)
    with pytest.raises(CloneTooLarge):
        await c.sharing.clone(owner_id=BOB, token=await _token(c, root))
    assert await c.decks.list_decks(BOB) == []


async def _rig(
    clone_allowance: int,
) -> tuple[SharingService, DeckService, InMemoryNoteRepository, InMemoryUserRepository]:
    clock = SystemClock()
    decks = DeckService(InMemoryDeckRepository(), clock)
    note_repo, card_repo = InMemoryNoteRepository(), InMemoryCardRepository()
    notes = NoteService(decks, note_repo, card_repo, clock)
    users = InMemoryUserRepository()
    await users.add(_user(ALICE))
    await users.add(_user(BOB))
    service = SharingService(
        decks, notes, note_repo, card_repo, InMemoryShareLinkRepository(), users,
        InMemoryQuotaRepository(limit=clone_allowance), clock,
    )
    return service, decks, note_repo, users


async def test_the_daily_clone_allowance_counts_cards() -> None:
    service, decks, note_repo, _users = await _rig(clone_allowance=3)
    deck = await decks.create(owner_id=ALICE, name="Cloze")
    await note_repo.add(
        Note(
            id="n", owner_id=ALICE, deck_id=deck.id, note_type=NoteType.CLOZE,
            fields=(("text", "{{c1::a}} {{c2::b}}"),), created_at=NOW,
        )
    )
    token = (await service.create(owner_id=ALICE, deck_id=deck.id, allow_clone=True)).raw_token

    await service.clone(owner_id=BOB, token=token)  # 2 cards of 3
    with pytest.raises(CloneLimitReached):
        await service.clone(owner_id=BOB, token=token)  # 2 more would exceed 3
    assert len(await decks.list_decks(BOB)) == 1


async def test_an_invalid_source_note_leaves_no_partial_clone() -> None:
    service, decks, note_repo, _users = await _rig(clone_allowance=100)
    root = await decks.create(owner_id=ALICE, name="Root")
    child = await decks.create(owner_id=ALICE, name="Child", parent_id=root.id)
    # A note that no longer passes validation (stored before a rule tightened).
    await note_repo.add(
        Note(
            id="bad", owner_id=ALICE, deck_id=child.id, note_type=NoteType.CLOZE,
            fields=(("text", "no deletions here"),), created_at=NOW,
        )
    )
    token = (await service.create(owner_id=ALICE, deck_id=root.id, allow_clone=True)).raw_token

    with pytest.raises(InvalidNote):
        await service.clone(owner_id=BOB, token=token)

    assert await decks.list_decks(BOB) == []


async def test_deleting_the_deck_deletes_its_links() -> None:
    c = await _container()
    root, child = await _shared_tree(c)
    tokens = [await _token(c, deck, allow_clone=False) for deck in (root, child)]

    await c.library.delete_deck(owner_id=ALICE, deck_id=root)

    for token in tokens:
        with pytest.raises(ShareNotFound):
            await c.sharing.preview(token=token)
