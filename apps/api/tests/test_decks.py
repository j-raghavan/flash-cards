# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import InMemoryDeckRepository
from flashcards.application.deck_service import DeckService
from flashcards.domain.decks import Deck, DeckCycle, DeckNotFound, InvalidDeckName

START = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
ALICE = "user-alice"
BOB = "user-bob"


class FakeClock:
    def __init__(self, now: datetime = START) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def repo() -> InMemoryDeckRepository:
    return InMemoryDeckRepository()


@pytest.fixture
def service(repo: InMemoryDeckRepository, clock: FakeClock) -> DeckService:
    return DeckService(repo, clock)


async def test_create_root_deck(service: DeckService) -> None:
    deck = await service.create(owner_id=ALICE, name="  Biology  ")
    assert deck.owner_id == ALICE
    assert deck.name == "Biology"
    assert deck.parent_id is None


async def test_create_nested_deck(service: DeckService) -> None:
    parent = await service.create(owner_id=ALICE, name="Biology")
    child = await service.create(owner_id=ALICE, name="Cells", parent_id=parent.id)
    assert child.parent_id == parent.id


async def test_create_rejects_blank_name(service: DeckService) -> None:
    with pytest.raises(InvalidDeckName):
        await service.create(owner_id=ALICE, name="   ")


async def test_create_rejects_overlong_name(service: DeckService) -> None:
    with pytest.raises(InvalidDeckName):
        await service.create(owner_id=ALICE, name="x" * 201)


async def test_create_under_missing_parent_is_not_found(service: DeckService) -> None:
    with pytest.raises(DeckNotFound):
        await service.create(owner_id=ALICE, name="Cells", parent_id="ghost")


async def test_cannot_nest_under_another_owners_deck(service: DeckService) -> None:
    bob_deck = await service.create(owner_id=BOB, name="Bob's")
    with pytest.raises(DeckNotFound):
        await service.create(owner_id=ALICE, name="Sneaky", parent_id=bob_deck.id)


async def test_list_is_owner_scoped_and_ordered(
    service: DeckService, clock: FakeClock
) -> None:
    first = await service.create(owner_id=ALICE, name="First")
    clock.advance(timedelta(minutes=1))
    second = await service.create(owner_id=ALICE, name="Second")
    await service.create(owner_id=BOB, name="Bob only")
    listed = await service.list_decks(ALICE)
    assert [d.id for d in listed] == [first.id, second.id]


async def test_get_missing_or_foreign_is_not_found(service: DeckService) -> None:
    bob_deck = await service.create(owner_id=BOB, name="Bob's")
    with pytest.raises(DeckNotFound):
        await service.get(owner_id=ALICE, deck_id=bob_deck.id)
    with pytest.raises(DeckNotFound):
        await service.get(owner_id=ALICE, deck_id="ghost")


async def test_rename(service: DeckService) -> None:
    deck = await service.create(owner_id=ALICE, name="Old")
    renamed = await service.rename(owner_id=ALICE, deck_id=deck.id, name="New")
    assert renamed.name == "New"
    assert (await service.get(owner_id=ALICE, deck_id=deck.id)).name == "New"


async def test_rename_rejects_bad_name(service: DeckService) -> None:
    deck = await service.create(owner_id=ALICE, name="Old")
    with pytest.raises(InvalidDeckName):
        await service.rename(owner_id=ALICE, deck_id=deck.id, name="")


async def test_move_to_root_and_under_parent(service: DeckService) -> None:
    a = await service.create(owner_id=ALICE, name="A")
    b = await service.create(owner_id=ALICE, name="B")
    moved = await service.move(owner_id=ALICE, deck_id=b.id, new_parent_id=a.id)
    assert moved.parent_id == a.id
    back = await service.move(owner_id=ALICE, deck_id=b.id, new_parent_id=None)
    assert back.parent_id is None


async def test_move_onto_self_is_a_cycle(service: DeckService) -> None:
    a = await service.create(owner_id=ALICE, name="A")
    with pytest.raises(DeckCycle):
        await service.move(owner_id=ALICE, deck_id=a.id, new_parent_id=a.id)


async def test_move_under_descendant_is_a_cycle(service: DeckService) -> None:
    a = await service.create(owner_id=ALICE, name="A")
    b = await service.create(owner_id=ALICE, name="B", parent_id=a.id)
    with pytest.raises(DeckCycle):
        await service.move(owner_id=ALICE, deck_id=a.id, new_parent_id=b.id)


async def test_move_under_foreign_deck_is_not_found(service: DeckService) -> None:
    a = await service.create(owner_id=ALICE, name="A")
    bob = await service.create(owner_id=BOB, name="Bob")
    with pytest.raises(DeckNotFound):
        await service.move(owner_id=ALICE, deck_id=a.id, new_parent_id=bob.id)


async def test_move_tolerates_a_dangling_ancestor(
    service: DeckService, repo: InMemoryDeckRepository, clock: FakeClock
) -> None:
    # A deck whose parent points at a missing deck: the cycle walk stops at the gap.
    orphan = Deck(
        id="orphan", owner_id=ALICE, name="Orphan", parent_id="ghost", created_at=clock.now()
    )
    await repo.add(orphan)
    mover = await service.create(owner_id=ALICE, name="Mover")
    moved = await service.move(owner_id=ALICE, deck_id=mover.id, new_parent_id="orphan")
    assert moved.parent_id == "orphan"


async def test_delete_removes_the_whole_subtree(service: DeckService) -> None:
    a = await service.create(owner_id=ALICE, name="A")
    b = await service.create(owner_id=ALICE, name="B", parent_id=a.id)
    c = await service.create(owner_id=ALICE, name="C", parent_id=b.id)
    sibling = await service.create(owner_id=ALICE, name="Sibling")
    removed = await service.delete(owner_id=ALICE, deck_id=a.id)
    assert set(removed) == {a.id, b.id, c.id}
    remaining = {d.id for d in await service.list_decks(ALICE)}
    assert remaining == {sibling.id}


async def test_delete_foreign_deck_is_not_found(service: DeckService) -> None:
    bob = await service.create(owner_id=BOB, name="Bob")
    with pytest.raises(DeckNotFound):
        await service.delete(owner_id=ALICE, deck_id=bob.id)
