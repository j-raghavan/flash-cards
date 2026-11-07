# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flashcards.adapters.memory import (
    InMemoryInviteRepository,
    InMemorySessionRepository,
    InMemoryUserRepository,
    SystemClock,
)
from flashcards.domain.invites import Invite, Role
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
