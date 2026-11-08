# SPDX-License-Identifier: AGPL-3.0-only
"""Integration tests for the export read-paths against a real Postgres (needs Docker)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from flashcards.adapters.db import Base, create_engine, create_sessionmaker
from flashcards.adapters.sql import (
    SqlAiJobRepository,
    SqlCardRepository,
    SqlDeckRepository,
    SqlNoteRepository,
    SqlReviewLogRepository,
    SqlSessionRepository,
    SqlUserRepository,
)
from flashcards.domain.ai import Thoroughness
from flashcards.domain.decks import Deck
from flashcards.domain.invites import Role
from flashcards.domain.jobs import new_generation_job
from flashcards.domain.notes import Card, Note, NoteType
from flashcards.domain.scheduling import Rating, ReviewLog
from flashcards.domain.sessions import Session
from flashcards.domain.users import User

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def test_note_list_for_deck_and_review_log_list_for_owner(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlDeckRepository(s).add(
            Deck(id="d1", owner_id="alice", name="Bio", parent_id=None, created_at=NOW)
        )
        notes = SqlNoteRepository(s)
        await notes.add(
            Note(
                id="n1", owner_id="alice", deck_id="d1", note_type=NoteType.BASIC,
                fields=(("front", "q"), ("back", "a")), created_at=NOW,
            )
        )
        await SqlCardRepository(s).add(
            Card(
                id="c1", owner_id="alice", deck_id="d1", note_id="n1", ordinal=0,
                created_at=NOW, suspended_at=None,
            )
        )
        logs = SqlReviewLogRepository(s)
        await logs.append(
            ReviewLog(
                id="r2", owner_id="alice", card_id="c1",
                rating=Rating.GOOD, reviewed_at=NOW + timedelta(hours=1),
            )
        )
        await logs.append(
            ReviewLog(
                id="r1", owner_id="alice", card_id="c1", rating=Rating.AGAIN, reviewed_at=NOW
            )
        )
        await logs.append(
            ReviewLog(
                id="rb", owner_id="bob", card_id="c1", rating=Rating.EASY, reviewed_at=NOW
            )
        )

    async with sm() as s:
        assert [n.id for n in await SqlNoteRepository(s).list_for_deck("d1")] == ["n1"]
        assert await SqlNoteRepository(s).list_for_deck("missing") == []
        assert await SqlNoteRepository(s).list_for_deck("d1", limit=0) == []
        assert await SqlNoteRepository(s).count_for_deck("d1") == 1
        assert await SqlNoteRepository(s).count_for_deck("missing") == 0
        # Oldest first, and scoped to the owner.
        owner_logs = await SqlReviewLogRepository(s).list_for_owner("alice")
        assert [log.id for log in owner_logs] == ["r1", "r2"]
        assert owner_logs[0].rating is Rating.AGAIN


async def test_user_session_and_aijob_deletion(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlUserRepository(s).add(
            User(id="u1", email="u@e.com", password_hash="h", role=Role.MEMBER, created_at=NOW)
        )
        sessions = SqlSessionRepository(s)
        for sid, token in (("s1", "t1"), ("s2", "t2")):
            await sessions.add(
                Session(
                    id=sid, user_id="u1", token_hash=token, created_at=NOW,
                    expires_at=NOW + timedelta(days=1), revoked_at=None,
                )
            )
        await SqlAiJobRepository(s).add(
            new_generation_job(
                owner_id="u1", source_id="src", draft_deck_id="d",
                thoroughness=Thoroughness.STANDARD, now=NOW,
            )
        )

    async with sm() as s:
        assert len(await SqlAiJobRepository(s).list_for_owner("u1")) == 1

    async with sm() as s, s.begin():
        await SqlSessionRepository(s).delete_for_user("u1")
        jobs = SqlAiJobRepository(s)
        for job in await jobs.list_for_owner("u1"):
            await jobs.delete(job.id)
        await SqlUserRepository(s).delete("u1")

    async with sm() as s:
        assert await SqlUserRepository(s).get("u1") is None
        assert await SqlSessionRepository(s).list_for_user("u1") == []
        assert await SqlAiJobRepository(s).list_for_owner("u1") == []


async def test_mock_exam_roundtrip_and_owner_delete(engine: AsyncEngine) -> None:
    from flashcards.adapters.sql import SqlMockExamRepository
    from flashcards.domain.mock_exam import ExamQuestion, MockExam

    sm = create_sessionmaker(engine)
    exam = MockExam(
        id="e1", owner_id="alice", deck_id="d1", created_at=NOW, time_limit_seconds=600,
        questions=(
            ExamQuestion(id="q1", prompt="p1", reference="r1", topic="Bio"),
            ExamQuestion(id="q2", prompt="p2", reference="r2", topic="Chem"),
        ),
    )
    async with sm() as s, s.begin():
        await SqlMockExamRepository(s).add(exam)
        await SqlMockExamRepository(s).add(
            MockExam(
                id="e2", owner_id="bob", deck_id="d9", created_at=NOW, time_limit_seconds=None,
                questions=(ExamQuestion(id="q", prompt="p", reference="r", topic="t"),),
            )
        )

    async with sm() as s:
        loaded = await SqlMockExamRepository(s).get("e1")
        assert loaded is not None
        assert loaded.time_limit_seconds == 600 and loaded.submitted_at is None
        assert [e.id for e in await SqlMockExamRepository(s).list_for_owner("alice")] == ["e1"]

    async with sm() as s, s.begin():
        # A conditional update: only the first submission is recorded.
        assert await SqlMockExamRepository(s).try_mark_submitted("e1", NOW) is True
        assert await SqlMockExamRepository(s).try_mark_submitted("e1", NOW) is False
        assert await SqlMockExamRepository(s).try_mark_submitted("missing", NOW) is False
    async with sm() as s:
        marked = await SqlMockExamRepository(s).get("e1")
        assert marked is not None and marked.submitted_at == NOW
        assert [q.reference for q in loaded.questions] == ["r1", "r2"]
        assert await SqlMockExamRepository(s).get("missing") is None

    async with sm() as s, s.begin():
        await SqlMockExamRepository(s).delete_for_deck("d9")
    async with sm() as s:
        assert await SqlMockExamRepository(s).get("e2") is None  # Bob's exam was in d9
        assert await SqlMockExamRepository(s).get("e1") is not None

    async with sm() as s, s.begin():
        await SqlMockExamRepository(s).delete_for_owner("alice")

    async with sm() as s:
        assert await SqlMockExamRepository(s).get("e1") is None


async def test_passage_repository(engine: AsyncEngine) -> None:
    from flashcards.adapters.sql import SqlPassageRepository
    from flashcards.domain.chat import Passage

    def passage(pid: str, owner: str, deck: str, file_id: str, page: int, ordinal: int) -> Passage:
        return Passage(
            id=pid, owner_id=owner, deck_id=deck, file_id=file_id, page=page,
            ordinal=ordinal, text=f"text {pid}",
        )

    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlPassageRepository(s).add_many(
            [
                passage("p3", "alice", "d1", "f2", 1, 0),
                passage("p2", "alice", "d1", "f1", 2, 0),
                passage("p1", "alice", "d1", "f1", 1, 1),
                passage("p0", "alice", "d1", "f1", 1, 0),
                passage("pb", "bob", "d9", "fb", 1, 0),
            ]
        )

    async with sm() as s:
        repo = SqlPassageRepository(s)
        assert [p.id for p in await repo.list_for_deck("d1")] == ["p0", "p1", "p2", "p3"]
        assert await repo.indexed_files("d1") == {"f1", "f2"}
        assert await repo.indexed_files("none") == set()

    async with sm() as s, s.begin():
        # The same positions again (a concurrent indexer) are ignored, not an error.
        await SqlPassageRepository(s).add_many([passage("dup", "alice", "d1", "f1", 1, 0)])
        await SqlPassageRepository(s).add_many([])
    async with sm() as s:
        assert len(await SqlPassageRepository(s).list_for_deck("d1")) == 4

    async with sm() as s, s.begin():
        await SqlPassageRepository(s).delete_for_deck("d1")
    async with sm() as s:
        assert await SqlPassageRepository(s).list_for_deck("d1") == []
        assert len(await SqlPassageRepository(s).list_for_deck("d9")) == 1

    async with sm() as s, s.begin():
        await SqlPassageRepository(s).add_many([passage("p0", "alice", "d1", "f1", 1, 0)])
        await SqlPassageRepository(s).delete_for_owner("alice")

    async with sm() as s:
        assert await SqlPassageRepository(s).list_for_deck("d1") == []
        assert [p.id for p in await SqlPassageRepository(s).list_for_deck("d9")] == ["pb"]


async def test_media_roundtrip(engine: AsyncEngine) -> None:
    from flashcards.adapters.sql import SqlMediaRepository
    from flashcards.domain.media import MediaAsset

    sm = create_sessionmaker(engine)
    asset = MediaAsset(
        id="m1", owner_id="alice", content_type="image/png", data=b"\x89PNG-bytes",
        created_at=NOW,
    )
    async with sm() as s, s.begin():
        await SqlMediaRepository(s).add(asset)
    async with sm() as s:
        loaded = await SqlMediaRepository(s).get("m1")
        assert loaded is not None
        assert loaded.content_type == "image/png" and loaded.data == b"\x89PNG-bytes"
        assert await SqlMediaRepository(s).get("missing") is None


async def test_owner_scoped_deletes(engine: AsyncEngine) -> None:
    from flashcards.adapters.sql import (
        SqlMediaRepository,
        SqlQuotaRepository,
        SqlReviewLogRepository,
    )
    from flashcards.domain.media import MediaAsset

    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        media = SqlMediaRepository(s)
        await media.add(
            MediaAsset(
                id="m1", owner_id="alice", content_type="image/png", data=b"x", created_at=NOW
            )
        )
        await media.add(
            MediaAsset(
                id="m2", owner_id="bob", content_type="image/png", data=b"y", created_at=NOW
            )
        )
        # A deck+note+card so the review log's FK is satisfied, then an owner-scoped log.
        await SqlDeckRepository(s).add(
            Deck(id="dk", owner_id="alice", name="D", parent_id=None, created_at=NOW)
        )
        await SqlNoteRepository(s).add(
            Note(
                id="n", owner_id="alice", deck_id="dk", note_type=NoteType.BASIC,
                fields=(("front", "q"), ("back", "a")), created_at=NOW,
            )
        )
        await SqlCardRepository(s).add(
            Card(
                id="ck", owner_id="alice", deck_id="dk", note_id="n", ordinal=0,
                created_at=NOW, suspended_at=None,
            )
        )
        await SqlReviewLogRepository(s).append(
            ReviewLog(id="rl", owner_id="alice", card_id="ck", rating=Rating.GOOD, reviewed_at=NOW)
        )
        assert await SqlQuotaRepository(s).try_reserve_pages("alice", 2, "2026-11") is True

    async with sm() as s:
        assert [m.id for m in await SqlMediaRepository(s).list_for_owner("alice")] == ["m1"]

    async with sm() as s, s.begin():
        await SqlMediaRepository(s).delete_for_owner("alice")
        await SqlReviewLogRepository(s).delete_for_owner("alice")
        await SqlQuotaRepository(s).delete_for_owner("alice")

    async with sm() as s:
        assert await SqlMediaRepository(s).list_for_owner("alice") == []
        assert await SqlReviewLogRepository(s).list_for_owner("alice") == []
        # Alice's quota counter is gone, so a fresh reservation succeeds.
        assert await SqlQuotaRepository(s).try_reserve_pages("alice", 1, "2026-11") is True
        # Bob's media untouched.
        assert [m.id for m in await SqlMediaRepository(s).list_for_owner("bob")] == ["m2"]


async def test_share_link_repository(engine: AsyncEngine) -> None:
    from flashcards.adapters.sql import SqlShareLinkRepository
    from flashcards.domain.sharing import ShareLink

    def link(lid: str, owner: str, deck: str, minutes: int) -> ShareLink:
        return ShareLink(
            id=lid, owner_id=owner, deck_id=deck, token_hash=f"hash-{lid}",
            allow_clone=False, created_at=NOW + timedelta(minutes=minutes),
        )

    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        repo = SqlShareLinkRepository(s)
        await repo.add(link("l2", "alice", "d1", 2))
        await repo.add(link("l1", "alice", "d1", 1))
        await repo.add(link("l3", "alice", "d2", 3))
        await repo.add(link("lb", "bob", "d9", 0))

    async with sm() as s:
        repo = SqlShareLinkRepository(s)
        assert [x.id for x in await repo.list_for_deck("d1")] == ["l1", "l2"]
        assert [x.id for x in await repo.list_for_owner("alice")] == ["l1", "l2", "l3"]
        found = await repo.get_by_token_hash("hash-l3")
        assert found is not None and found.deck_id == "d2"
        assert await repo.get_by_token_hash("nope") is None
        assert await repo.get("missing") is None

    async with sm() as s, s.begin():
        repo = SqlShareLinkRepository(s)
        current = await repo.get("l1")
        assert current is not None
        await repo.update(current.with_allow_clone(True).revoked(NOW))
    async with sm() as s:
        updated = await SqlShareLinkRepository(s).get("l1")
        assert updated is not None and updated.allow_clone and updated.revoked_at == NOW

    async with sm() as s, s.begin():
        await SqlShareLinkRepository(s).delete_for_deck("d1")
        await SqlShareLinkRepository(s).delete_for_owner("alice")
    async with sm() as s:
        repo = SqlShareLinkRepository(s)
        assert await repo.list_for_owner("alice") == []
        assert [x.id for x in await repo.list_for_owner("bob")] == ["lb"]


async def test_audit_repository(engine: AsyncEngine) -> None:
    from flashcards.adapters.sql import SqlAuditRepository
    from flashcards.domain.audit import AuditAction, AuditEvent

    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        repo = SqlAuditRepository(s)
        await repo.append(AuditEvent(id="e1", action=AuditAction.LOGIN_LOCKED, created_at=NOW))
        await repo.append(
            AuditEvent(
                id="e2", action=AuditAction.USER_SUSPENDED, created_at=NOW + timedelta(minutes=1),
                actor_id="admin", target_id="u1", detail=(("reason", "abuse"),),
            )
        )
    async with sm() as s:
        repo = SqlAuditRepository(s)
        recent = await repo.list_recent(limit=10)
        assert [e.id for e in recent] == ["e2", "e1"]
        assert recent[0].detail == (("reason", "abuse"),) and recent[0].actor_id == "admin"
        assert [e.id for e in await repo.list_recent(limit=1)] == ["e2"]
        older = await repo.list_recent(limit=10, before=NOW + timedelta(minutes=1))
        assert [e.id for e in older] == ["e1"]


async def test_login_throttle_repository(engine: AsyncEngine) -> None:
    from flashcards.adapters.sql import SqlLoginThrottleRepository
    from flashcards.domain.login_throttle import (
        FAILURE_WINDOW,
        LOCKOUT,
        MAX_FAILED_LOGINS,
        admitted,
    )

    sm = create_sessionmaker(engine)

    async def attempt(key: str, at: datetime) -> tuple[int, datetime | None]:
        async with sm() as s, s.begin():
            state = await SqlLoginThrottleRepository(s).register_attempt(key, at)
        return state.failures, state.locked_until

    # The atomic upsert follows the same rules as the in-memory domain function.
    for n in range(1, MAX_FAILED_LOGINS):
        assert await attempt("k", NOW) == (n, None)
    assert await attempt("k", NOW) == (MAX_FAILED_LOGINS, NOW + LOCKOUT)
    later = NOW + timedelta(minutes=5)
    assert await attempt("k", later) == (MAX_FAILED_LOGINS + 1, NOW + LOCKOUT)
    assert await attempt("k", NOW + LOCKOUT) == (1, None)  # an expired lock starts fresh
    assert await attempt("k", NOW + LOCKOUT + FAILURE_WINDOW) == (1, None)  # so does a window

    async with sm() as s:
        state = await SqlLoginThrottleRepository(s).get("k")
        assert state is not None and admitted(state)
        assert await SqlLoginThrottleRepository(s).get("missing") is None
    async with sm() as s, s.begin():
        await SqlLoginThrottleRepository(s).delete("k")
    async with sm() as s:
        assert await SqlLoginThrottleRepository(s).get("k") is None


async def test_user_token_user_and_session_updates(engine: AsyncEngine) -> None:
    from flashcards.adapters.sql import SqlUserTokenRepository
    from flashcards.domain.user_tokens import TokenPurpose, UserToken

    def token(tid: str, minutes: int, purpose: TokenPurpose) -> UserToken:
        return UserToken(
            id=tid, user_id="u1", purpose=purpose, token_hash=f"h-{tid}",
            created_at=NOW + timedelta(minutes=minutes), expires_at=NOW + timedelta(hours=1),
        )

    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlUserRepository(s).add(
            User(id="u1", email="u@e.com", password_hash="h", role=Role.MEMBER, created_at=NOW)
        )
        await SqlUserRepository(s).add(
            User(
                id="u2", email="v@e.com", password_hash="h", role=Role.MEMBER,
                created_at=NOW + timedelta(minutes=1),
            )
        )
        for sid in ("s1", "s2"):
            await SqlSessionRepository(s).add(
                Session(
                    id=sid, user_id="u1", token_hash=f"t-{sid}", created_at=NOW,
                    expires_at=NOW + timedelta(days=1), revoked_at=None,
                )
            )
        tokens = SqlUserTokenRepository(s)
        await tokens.add(token("a", 0, TokenPurpose.PASSWORD_RESET))
        await tokens.add(token("b", 1, TokenPurpose.PASSWORD_RESET))
        await tokens.add(token("c", 2, TokenPurpose.EMAIL_VERIFICATION))

    async with sm() as s, s.begin():
        users = SqlUserRepository(s)
        assert [u.id for u in await users.list_all()] == ["u1", "u2"]
        current = await users.get("u1")
        assert current is not None
        await users.update(current.with_verified(NOW).with_password_hash("new"))
        await users.update(
            User(id="ghost", email="g@e.com", password_hash="h", role=Role.MEMBER, created_at=NOW)
        )  # updating a missing user is a no-op
        await SqlSessionRepository(s).revoke_all_for_user("u1", NOW)
        tokens = SqlUserTokenRepository(s)
        latest = await tokens.latest_for_user("u1", TokenPurpose.PASSWORD_RESET)
        assert latest is not None and latest.id == "b"
        assert await tokens.try_use("b", NOW) is True
        assert await tokens.try_use("b", NOW) is False
        await tokens.invalidate_for_user("u1", TokenPurpose.PASSWORD_RESET, NOW)

    async with sm() as s:
        stored = await SqlUserRepository(s).get("u1")
        assert stored is not None and stored.is_verified and stored.password_hash == "new"
        assert await SqlUserRepository(s).get("ghost") is None
        sessions = await SqlSessionRepository(s).list_for_user("u1")
        assert all(x.revoked_at is not None for x in sessions)
        tokens = SqlUserTokenRepository(s)
        a = await tokens.get_by_token_hash("h-a")
        c = await tokens.get_by_token_hash("h-c")
        assert a is not None and a.used_at is not None  # invalidated
        assert c is not None and c.used_at is None  # other purpose untouched
        assert await tokens.get_by_token_hash("missing") is None

    async with sm() as s, s.begin():
        await SqlUserTokenRepository(s).delete_for_user("u1")
    async with sm() as s:
        assert await SqlUserTokenRepository(s).latest_for_user(
            "u1", TokenPurpose.EMAIL_VERIFICATION
        ) is None
