# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import (
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryInviteRepository,
    InMemoryNoteRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
    InMemorySessionRepository,
    InMemoryUserRepository,
    SystemClock,
)
from flashcards.domain.decks import Deck
from flashcards.domain.invites import Invite, Role
from flashcards.domain.notes import Card, Note, NoteType
from flashcards.domain.scheduling import (
    CardSchedule,
    DuplicateReview,
    Rating,
    ReviewLog,
    ScheduleState,
)
from flashcards.domain.sessions import Session
from flashcards.domain.users import EmailAlreadyRegistered, User

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def make_session(
    session_id: str = "sess-1", user_id: str = "user-1", digest: str = "sess-hash-1"
) -> Session:
    return Session(
        id=session_id,
        user_id=user_id,
        token_hash=digest,
        created_at=NOW,
        expires_at=NOW + timedelta(days=14),
    )


def make_user(user_id: str = "user-1", email: str = "person@example.com") -> User:
    return User(
        id=user_id,
        email=email,
        password_hash="hashed",
        role=Role.MEMBER,
        created_at=NOW,
    )


def make_invite(invite_id: str = "inv-1", digest: str = "hash-1") -> Invite:
    return Invite(
        id=invite_id,
        token_hash=digest,
        role=Role.MEMBER,
        created_by="admin-1",
        created_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )


def test_system_clock_returns_timezone_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


async def test_get_returns_none_for_unknown_id() -> None:
    assert await InMemoryInviteRepository().get("missing") is None


async def test_get_by_token_hash_finds_the_right_invite_among_many() -> None:
    repo = InMemoryInviteRepository()
    await repo.add(make_invite("inv-1", "hash-1"))
    await repo.add(make_invite("inv-2", "hash-2"))
    found = await repo.get_by_token_hash("hash-2")
    assert found is not None
    assert found.id == "inv-2"


async def test_get_by_token_hash_returns_none_when_no_match() -> None:
    repo = InMemoryInviteRepository()
    await repo.add(make_invite())
    assert await repo.get_by_token_hash("other") is None


async def test_try_redeem_unknown_invite_is_false() -> None:
    assert await InMemoryInviteRepository().try_redeem("missing", NOW) is False


async def test_try_redeem_is_single_use() -> None:
    repo = InMemoryInviteRepository()
    await repo.add(make_invite())
    assert await repo.try_redeem("inv-1", NOW) is True
    assert await repo.try_redeem("inv-1", NOW) is False


async def test_try_redeem_refuses_revoked_invite() -> None:
    repo = InMemoryInviteRepository()
    await repo.add(make_invite())
    assert await repo.try_revoke("inv-1", NOW) is True
    assert await repo.try_redeem("inv-1", NOW) is False


async def test_try_revoke_unknown_invite_is_false() -> None:
    assert await InMemoryInviteRepository().try_revoke("missing", NOW) is False


async def test_try_revoke_twice_only_changes_once() -> None:
    repo = InMemoryInviteRepository()
    await repo.add(make_invite())
    assert await repo.try_revoke("inv-1", NOW) is True
    assert await repo.try_revoke("inv-1", NOW) is False


async def test_invite_repo_list_all_newest_first() -> None:
    repo = InMemoryInviteRepository()
    first = Invite(
        id="inv-1",
        token_hash="h1",
        role=Role.MEMBER,
        created_by="admin-1",
        created_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )
    second = Invite(
        id="inv-2",
        token_hash="h2",
        role=Role.MEMBER,
        created_by="admin-1",
        created_at=NOW + timedelta(hours=1),
        expires_at=NOW + timedelta(days=7),
    )
    await repo.add(first)
    await repo.add(second)
    assert [inv.id for inv in await repo.list_all()] == ["inv-2", "inv-1"]


async def test_user_repo_get_returns_none_for_unknown_id() -> None:
    assert await InMemoryUserRepository().get("missing") is None


async def test_user_repo_get_by_email_returns_none_when_absent() -> None:
    assert await InMemoryUserRepository().get_by_email("nobody@example.com") is None


async def test_user_repo_add_then_fetch_by_id_and_email() -> None:
    repo = InMemoryUserRepository()
    await repo.add(make_user())
    assert await repo.get("user-1") is not None
    found = await repo.get_by_email("person@example.com")
    assert found is not None
    assert found.id == "user-1"


async def test_user_repo_rejects_duplicate_email() -> None:
    repo = InMemoryUserRepository()
    await repo.add(make_user("user-1", "dup@example.com"))
    with pytest.raises(EmailAlreadyRegistered):
        await repo.add(make_user("user-2", "dup@example.com"))


async def test_session_repo_finds_by_token_hash() -> None:
    repo = InMemorySessionRepository()
    await repo.add(make_session("sess-1", digest="hash-a"))
    await repo.add(make_session("sess-2", digest="hash-b"))
    found = await repo.get_by_token_hash("hash-b")
    assert found is not None
    assert found.id == "sess-2"


async def test_session_repo_get_by_token_hash_returns_none_when_absent() -> None:
    assert await InMemorySessionRepository().get_by_token_hash("nope") is None


async def test_session_repo_lists_user_sessions_newest_first() -> None:
    repo = InMemorySessionRepository()
    older = Session(
        id="sess-old",
        user_id="user-1",
        token_hash="h1",
        created_at=NOW,
        expires_at=NOW + timedelta(days=14),
    )
    newer = Session(
        id="sess-new",
        user_id="user-1",
        token_hash="h2",
        created_at=NOW + timedelta(hours=1),
        expires_at=NOW + timedelta(days=14),
    )
    other = make_session("sess-other", user_id="user-2", digest="h3")
    await repo.add(older)
    await repo.add(newer)
    await repo.add(other)
    listed = await repo.list_for_user("user-1")
    assert [s.id for s in listed] == ["sess-new", "sess-old"]


async def test_session_repo_revoke_is_single_use() -> None:
    repo = InMemorySessionRepository()
    await repo.add(make_session())
    assert await repo.revoke("sess-1", NOW) is True
    assert await repo.revoke("sess-1", NOW) is False


async def test_session_repo_revoke_unknown_is_false() -> None:
    assert await InMemorySessionRepository().revoke("missing", NOW) is False


# --- M1 in-memory repositories ---


async def test_deck_repo_crud_and_owner_scope() -> None:
    repo = InMemoryDeckRepository()
    assert await repo.get("missing") is None
    deck = Deck(id="d1", owner_id="alice", name="A", parent_id=None, created_at=NOW)
    await repo.add(deck)
    assert (await repo.get("d1")) is not None
    await repo.update(Deck(id="d1", owner_id="alice", name="B", parent_id=None, created_at=NOW))
    updated = await repo.get("d1")
    assert updated is not None and updated.name == "B"
    assert [d.id for d in await repo.list_for_owner("alice")] == ["d1"]
    assert await repo.list_for_owner("bob") == []
    await repo.delete("d1")
    assert await repo.get("d1") is None


async def test_note_repo_crud() -> None:
    repo = InMemoryNoteRepository()
    assert await repo.get("missing") is None
    note = Note(
        id="n1",
        owner_id="alice",
        deck_id="d1",
        note_type=NoteType.BASIC,
        fields=(("front", "Q"), ("back", "A")),
        created_at=NOW,
    )
    await repo.add(note)
    assert (await repo.get("n1")) is not None
    await repo.delete("n1")
    assert await repo.get("n1") is None


def _card(card_id: str, note_id: str = "n1", deck_id: str = "d1") -> Card:
    return Card(
        id=card_id,
        owner_id="alice",
        deck_id=deck_id,
        note_id=note_id,
        ordinal=0,
        created_at=NOW,
    )


async def test_card_repo_list_order_update_and_delete_for_note() -> None:
    repo = InMemoryCardRepository()
    assert await repo.get("missing") is None
    await repo.add(_card("c1", note_id="n1"))
    await repo.add(_card("c2", note_id="n2"))
    assert {c.id for c in await repo.list_for_deck("d1")} == {"c1", "c2"}
    await repo.update(_card("c1").suspended(NOW))
    suspended = await repo.get("c1")
    assert suspended is not None and suspended.is_suspended
    await repo.delete_for_note("n1")
    assert [c.id for c in await repo.list_for_deck("d1")] == ["c2"]


async def test_schedule_repo_upsert_and_get() -> None:
    repo = InMemoryScheduleRepository()
    assert await repo.get("c1") is None
    schedule = CardSchedule(
        card_id="c1",
        state=ScheduleState.LEARNING,
        step=0,
        stability=None,
        difficulty=None,
        due_at=NOW,
        last_reviewed_at=None,
    )
    await repo.upsert(schedule)
    loaded = await repo.get("c1")
    assert loaded is not None and loaded.state is ScheduleState.LEARNING
    await repo.upsert(
        CardSchedule(
            card_id="c1",
            state=ScheduleState.REVIEW,
            step=None,
            stability=5.0,
            difficulty=5.0,
            due_at=NOW + timedelta(days=1),
            last_reviewed_at=NOW,
        )
    )
    reloaded = await repo.get("c1")
    assert reloaded is not None and reloaded.state is ScheduleState.REVIEW


def _log(key: str | None, owner: str = "alice", card: str = "c1") -> ReviewLog:
    return ReviewLog(
        id=f"log-{key}-{card}",
        owner_id=owner,
        card_id=card,
        rating=Rating.GOOD,
        reviewed_at=NOW,
        idempotency_key=key,
    )


async def test_review_log_repo_append_and_duplicate_key() -> None:
    repo = InMemoryReviewLogRepository()
    await repo.append(_log("k1"))
    # Same (owner, card, key) is rejected atomically.
    with pytest.raises(DuplicateReview):
        await repo.append(_log("k1"))
    # Same key on a different card is allowed.
    await repo.append(_log("k1", card="c2"))
    # A keyless log is never deduplicated.
    await repo.append(_log(None))
    await repo.append(_log(None))
