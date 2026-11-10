# SPDX-License-Identifier: AGPL-3.0-only
"""The read access policy for the shared library (ADR 0024)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryMediaRepository,
    InMemoryNoteRepository,
    InMemoryNoteTypeRepository,
    InMemoryUserRepository,
)
from flashcards.application.read_access import ReadAccess
from flashcards.domain.access import ReadScope
from flashcards.domain.decks import Deck, DeckNotFound
from flashcards.domain.invites import Role
from flashcards.domain.media import MediaAsset, MediaNotFound
from flashcards.domain.note_types import CardTemplate, CustomNoteType, NoteTypeNotFound
from flashcards.domain.notes import Card, CardNotFound, Note, NoteNotFound, NoteType
from flashcards.domain.users import User

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
ADMIN, OTHER_ADMIN, MEMBER, OTHER_MEMBER = "admin", "admin-2", "member", "member-2"


def _user(user_id: str, role: Role) -> User:
    return User(
        id=user_id,
        email=f"{user_id}@example.com",
        password_hash="h",
        role=role,
        created_at=NOW,
    )


class _World:
    """Each owner has one deck holding one custom note, one card and one image."""

    def __init__(self) -> None:
        self.users = InMemoryUserRepository()
        self.decks = InMemoryDeckRepository()
        self.notes = InMemoryNoteRepository()
        self.cards = InMemoryCardRepository()
        self.note_types = InMemoryNoteTypeRepository()
        self.media = InMemoryMediaRepository()
        self.access = ReadAccess(
            self.users, self.decks, self.notes, self.cards, self.note_types, self.media
        )

    async def seed(self) -> None:
        owners = [(ADMIN, Role.ADMIN), (OTHER_ADMIN, Role.ADMIN)]
        owners += [(MEMBER, Role.MEMBER), (OTHER_MEMBER, Role.MEMBER)]
        for offset, (owner, role) in enumerate(owners):
            await self.users.add(_user(owner, role))
            created = NOW + timedelta(minutes=offset)
            await self.decks.add(
                Deck(
                    id=f"d-{owner}", owner_id=owner, name=owner, parent_id=None,
                    created_at=created,
                )
            )
            await self.note_types.add(
                CustomNoteType(
                    id=f"t-{owner}",
                    owner_id=owner,
                    name="Vocab",
                    fields=("Term",),
                    templates=(CardTemplate(0, "Card 1", "{{Term}}", "{{Term}}"),),
                    created_at=NOW,
                )
            )
            await self.notes.add(
                Note(
                    id=f"n-{owner}",
                    owner_id=owner,
                    deck_id=f"d-{owner}",
                    note_type=NoteType.CUSTOM,
                    fields=(("Term", "x"),),
                    created_at=NOW,
                    note_type_id=f"t-{owner}",
                )
            )
            await self.cards.add(
                Card(
                    id=f"c-{owner}",
                    owner_id=owner,
                    deck_id=f"d-{owner}",
                    note_id=f"n-{owner}",
                    ordinal=0,
                    created_at=NOW,
                )
            )
            await self.media.add(
                MediaAsset(
                    id=f"m-{owner}", owner_id=owner, content_type="image/png", data=b"p",
                    created_at=NOW,
                )
            )

    async def demote(self, user_id: str) -> None:
        user = await self.users.get(user_id)
        assert user is not None
        await self.users.update(user.with_role(Role.MEMBER))

    async def suspend(self, user_id: str) -> None:
        user = await self.users.get(user_id)
        assert user is not None
        await self.users.update(user.with_suspended(NOW))


@pytest.fixture
async def world() -> _World:
    built = _World()
    await built.seed()
    return built


Read = Callable[[ReadAccess, str, str], Awaitable[object]]


async def _deck(access: ReadAccess, user_id: str, owner: str) -> object:
    return await access.deck(user_id, f"d-{owner}")


async def _deck_cards(access: ReadAccess, user_id: str, owner: str) -> object:
    return await access.deck_cards(user_id, f"d-{owner}")


async def _card(access: ReadAccess, user_id: str, owner: str) -> object:
    return await access.card(user_id, f"c-{owner}")


async def _note(access: ReadAccess, user_id: str, owner: str) -> object:
    return await access.note(user_id, f"n-{owner}")


async def _note_type(access: ReadAccess, user_id: str, owner: str) -> object:
    return await access.note_type(user_id, f"t-{owner}")


async def _media(access: ReadAccess, user_id: str, owner: str) -> object:
    return await access.media(user_id, f"m-{owner}")


async def _render(access: ReadAccess, user_id: str, owner: str) -> object:
    return await access.render_card(user_id, f"c-{owner}")


READS: list[tuple[Read, type[Exception]]] = [
    (_deck, DeckNotFound),
    (_deck_cards, DeckNotFound),
    (_card, CardNotFound),
    (_note, NoteNotFound),
    (_note_type, NoteTypeNotFound),
    (_media, MediaNotFound),
    (_render, CardNotFound),
]


@pytest.mark.parametrize(("read", "_error"), READS)
async def test_own_and_active_admin_material_is_readable(
    world: _World, read: Read, _error: type[Exception]
) -> None:
    assert await read(world.access, MEMBER, MEMBER) is not None
    assert await read(world.access, MEMBER, ADMIN) is not None
    assert await read(world.access, ADMIN, OTHER_ADMIN) is not None  # admins read each other


@pytest.mark.parametrize(("read", "error"), READS)
async def test_another_members_material_is_not_found(
    world: _World, read: Read, error: type[Exception]
) -> None:
    with pytest.raises(error):
        await read(world.access, MEMBER, OTHER_MEMBER)
    with pytest.raises(error):
        await read(world.access, ADMIN, MEMBER)  # admins do not read members' decks
    with pytest.raises(error):
        await read(world.access, MEMBER, "nobody")  # missing


@pytest.mark.parametrize("change", ["demote", "suspend", "delete"])
@pytest.mark.parametrize(("read", "error"), READS)
async def test_an_admin_who_is_no_longer_active_leaves_the_library(
    world: _World, read: Read, error: type[Exception], change: str
) -> None:
    if change == "demote":
        await world.demote(ADMIN)
    elif change == "suspend":
        await world.suspend(ADMIN)
    else:
        await world.users.delete(ADMIN)

    with pytest.raises(error):
        await read(world.access, MEMBER, ADMIN)
    assert await read(world.access, MEMBER, OTHER_ADMIN) is not None
    if change != "delete":  # their own material stays theirs to read
        assert await read(world.access, ADMIN, ADMIN) is not None


async def test_render_card_returns_the_card_its_note_and_template(world: _World) -> None:
    card, note, template = await world.access.render_card(MEMBER, f"c-{ADMIN}")

    assert (card.id, note.id) == (f"c-{ADMIN}", f"n-{ADMIN}")
    assert template is not None and template.name == "Card 1"


async def test_render_card_of_a_built_in_note_has_no_template(world: _World) -> None:
    await world.notes.add(
        Note(
            id="basic", owner_id=ADMIN, deck_id=f"d-{ADMIN}", note_type=NoteType.BASIC,
            fields=(("front", "Q"), ("back", "A")), created_at=NOW,
        )
    )
    await world.cards.add(
        Card(id="c-basic", owner_id=ADMIN, deck_id=f"d-{ADMIN}", note_id="basic", ordinal=0,
             created_at=NOW)
    )

    assert (await world.access.render_card(MEMBER, "c-basic"))[2] is None


async def test_render_card_needs_a_readable_note_and_note_type(world: _World) -> None:
    # A card whose note, or whose note's type, is not readable is not rendered.
    await world.cards.add(
        Card(id="c-odd", owner_id=ADMIN, deck_id=f"d-{ADMIN}", note_id=f"n-{OTHER_MEMBER}",
             ordinal=0, created_at=NOW)
    )
    with pytest.raises(NoteNotFound):
        await world.access.render_card(MEMBER, "c-odd")
    await world.notes.add(
        Note(
            id="n-odd", owner_id=ADMIN, deck_id=f"d-{ADMIN}", note_type=NoteType.CUSTOM,
            fields=(("Term", "x"),), created_at=NOW, note_type_id=f"t-{OTHER_MEMBER}",
        )
    )
    await world.cards.add(
        Card(id="c-odd-type", owner_id=ADMIN, deck_id=f"d-{ADMIN}", note_id="n-odd",
             ordinal=0, created_at=NOW)
    )
    with pytest.raises(NoteTypeNotFound):
        await world.access.render_card(MEMBER, "c-odd-type")


async def test_decks_are_the_callers_and_the_librarys_oldest_first(world: _World) -> None:
    # Same created_at as the member's deck: the id breaks the tie.
    await world.decks.add(
        Deck(id="d-a-tie", owner_id=ADMIN, name="tie", parent_id=None,
             created_at=NOW + timedelta(minutes=2))
    )

    member_view = [deck.id for deck in await world.access.decks(MEMBER)]
    admin_view = [deck.id for deck in await world.access.decks(ADMIN)]

    assert member_view == [f"d-{ADMIN}", f"d-{OTHER_ADMIN}", "d-a-tie", f"d-{MEMBER}"]
    assert admin_view == [f"d-{ADMIN}", f"d-{OTHER_ADMIN}", "d-a-tie"]
    await world.demote(OTHER_ADMIN)
    assert [deck.id for deck in await world.access.decks(MEMBER)] == [
        f"d-{ADMIN}", "d-a-tie", f"d-{MEMBER}"
    ]


async def test_deck_cards_lists_a_library_decks_cards(world: _World) -> None:
    cards = await world.access.deck_cards(MEMBER, f"d-{ADMIN}")

    assert [card.id for card in cards] == [f"c-{ADMIN}"]


async def test_scope_lists_the_active_admins() -> None:
    users = InMemoryUserRepository()
    await users.add(_user("b-admin", Role.ADMIN))
    await users.add(_user("a-admin", Role.ADMIN))
    await users.add(_user("x-member", Role.MEMBER))
    await users.add(_user("c-admin", Role.ADMIN).with_suspended(NOW))
    access = ReadAccess(
        users,
        InMemoryDeckRepository(),
        InMemoryNoteRepository(),
        InMemoryCardRepository(),
        InMemoryNoteTypeRepository(),
        InMemoryMediaRepository(),
    )

    assert await users.list_active_admin_ids() == ["a-admin", "b-admin"]
    scope = await access.scope("x-member")
    assert scope == ReadScope("x-member", frozenset({"a-admin", "b-admin"}))


def test_read_scope_rules() -> None:
    scope = ReadScope("me", frozenset({"admin"}))

    assert scope.can_read("me") and scope.can_read("admin")
    assert not scope.can_read("someone")
    assert scope.owns("me") and not scope.owns("admin")
    assert scope.owner_ids == ("admin", "me")
    assert ReadScope("admin", frozenset({"admin"})).owner_ids == ("admin",)
