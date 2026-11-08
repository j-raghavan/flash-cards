# SPDX-License-Identifier: AGPL-3.0-only
"""Integration tests for the SQLAlchemy adapters against a real Postgres.

These require Docker: a throwaway Postgres container is started per test session.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flashcards.adapters.apkg import ApkgReader
from flashcards.adapters.db import (
    Base,
    create_engine,
    create_sessionmaker,
)
from flashcards.adapters.extraction import PyMuPDFExtractor
from flashcards.adapters.fsrs_scheduler import FsrsScheduler
from flashcards.adapters.llm import NullLLMProvider
from flashcards.adapters.media import PuremagicMediaSniffer
from flashcards.adapters.memory import InMemoryJobQueue, SystemClock
from flashcards.adapters.passwords import Argon2PasswordHasher
from flashcards.adapters.sql import (
    SqlAiJobRepository,
    SqlCardRepository,
    SqlDeckRepository,
    SqlDraftCardRepository,
    SqlGoalRepository,
    SqlInviteRepository,
    SqlMediaRepository,
    SqlNoteRepository,
    SqlObjectStore,
    SqlQuotaRepository,
    SqlReviewLogRepository,
    SqlScheduleRepository,
    SqlSessionRepository,
    SqlUserRepository,
)
from flashcards.api.app import create_app
from flashcards.api.container import Repositories, build_services
from flashcards.application.invite_service import InviteService
from flashcards.config import Settings
from flashcards.domain.decks import Deck
from flashcards.domain.invites import InviteStatus, Role
from flashcards.domain.ports import PasswordHasher
from flashcards.domain.sessions import Session
from flashcards.domain.users import EmailAlreadyRegistered, User


def _sql_repositories(session: AsyncSession, hasher: PasswordHasher) -> Repositories:
    return Repositories(
        clock=SystemClock(),
        hasher=hasher,
        scheduler=FsrsScheduler(),
        invites=SqlInviteRepository(session),
        users=SqlUserRepository(session),
        sessions=SqlSessionRepository(session),
        decks=SqlDeckRepository(session),
        notes=SqlNoteRepository(session),
        cards=SqlCardRepository(session),
        schedules=SqlScheduleRepository(session),
        review_logs=SqlReviewLogRepository(session),
        ai_jobs=SqlAiJobRepository(session),
        draft_cards=SqlDraftCardRepository(session),
        object_store=SqlObjectStore(session),
        quotas=SqlQuotaRepository(session),
        extractor=PyMuPDFExtractor(),
        job_queue=InMemoryJobQueue(),
        llm=NullLLMProvider(),
        goals=SqlGoalRepository(session),
        package_reader=ApkgReader(),
        media=SqlMediaRepository(session),
        media_sniffer=PuremagicMediaSniffer(),
    )

PASSWORD = "correct-horse-battery"


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


def _now() -> datetime:
    return SystemClock().now()


async def test_user_add_get_and_duplicate(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    user = User(
        id="u1",
        email="person@example.com",
        password_hash="hash",
        role=Role.MEMBER,
        created_at=SystemClock().now(),
    )
    async with sm() as s, s.begin():
        await SqlUserRepository(s).add(user)
    async with sm() as s:
        repo = SqlUserRepository(s)
        assert (await repo.get("u1")) is not None
        found = await repo.get_by_email("person@example.com")
        assert found is not None and found.id == "u1"
        assert (await repo.get("missing")) is None
        assert (await repo.get_by_email("none@example.com")) is None
    # Duplicate email is rejected.
    async with sm() as s, s.begin():
        with pytest.raises(EmailAlreadyRegistered):
            await SqlUserRepository(s).add(
                User(
                    id="u2",
                    email="person@example.com",
                    password_hash="hash",
                    role=Role.MEMBER,
                    created_at=SystemClock().now(),
                )
            )


async def test_invite_lifecycle(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        created = await InviteService(SqlInviteRepository(s), SystemClock()).create(
            created_by="admin", role=Role.ADMIN
        )
    invite_id = created.invite.id
    async with sm() as s:
        repo = SqlInviteRepository(s)
        assert (await repo.get(invite_id)) is not None
        assert (await repo.get_by_token_hash(created.invite.token_hash)) is not None
        assert [i.id for i in await repo.list_all()] == [invite_id]
    # Single-use redemption.
    async with sm() as s, s.begin():
        repo = SqlInviteRepository(s)
        assert await repo.try_redeem(invite_id, SystemClock().now()) is True
    async with sm() as s, s.begin():
        repo = SqlInviteRepository(s)
        assert await repo.try_redeem(invite_id, SystemClock().now()) is False


async def test_revoke_blocks_redeem(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        created = await InviteService(SqlInviteRepository(s), SystemClock()).create(
            created_by="admin"
        )
    async with sm() as s, s.begin():
        repo = SqlInviteRepository(s)
        assert await repo.try_revoke(created.invite.id, SystemClock().now()) is True
    async with sm() as s, s.begin():
        repo = SqlInviteRepository(s)
        assert await repo.try_revoke(created.invite.id, SystemClock().now()) is False
        assert await repo.try_redeem(created.invite.id, SystemClock().now()) is False


async def test_session_lifecycle(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    now = SystemClock().now()
    sess = Session(
        id="s1",
        user_id="u1",
        token_hash="th1",
        created_at=now,
        expires_at=now,
    )
    async with sm() as s, s.begin():
        await SqlSessionRepository(s).add(sess)
    async with sm() as s:
        repo = SqlSessionRepository(s)
        assert (await repo.get_by_token_hash("th1")) is not None
        assert (await repo.get_by_token_hash("nope")) is None
        assert [x.id for x in await repo.list_for_user("u1")] == ["s1"]
    async with sm() as s, s.begin():
        assert await SqlSessionRepository(s).revoke("s1", SystemClock().now()) is True
    async with sm() as s, s.begin():
        assert await SqlSessionRepository(s).revoke("s1", SystemClock().now()) is False
        assert await SqlSessionRepository(s).revoke("missing", SystemClock().now()) is False


async def test_registration_commits_atomically(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    hasher = Argon2PasswordHasher()
    async with sm() as s, s.begin():
        created = await InviteService(SqlInviteRepository(s), SystemClock()).create(
            created_by="admin", role=Role.MEMBER
        )
    async with sm() as s, s.begin():
        services = build_services(_sql_repositories(s, hasher))
        await services.registration.register(
            raw_token=created.raw_token, email="new@example.com", password=PASSWORD
        )
    async with sm() as s:
        assert (await SqlUserRepository(s).get_by_email("new@example.com")) is not None
        invite = await SqlInviteRepository(s).get(created.invite.id)
        assert invite is not None
        assert invite.status(SystemClock().now()) is InviteStatus.REDEEMED


async def test_unit_of_work_rolls_back_on_error(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        created = await InviteService(SqlInviteRepository(s), SystemClock()).create(
            created_by="admin"
        )
    with pytest.raises(RuntimeError):
        async with sm() as s, s.begin():
            assert await SqlInviteRepository(s).try_redeem(
                created.invite.id, SystemClock().now()
            ) is True
            raise RuntimeError("boom after redeem")
    # The redemption was rolled back with the failed transaction.
    async with sm() as s:
        invite = await SqlInviteRepository(s).get(created.invite.id)
        assert invite is not None
        assert invite.status(SystemClock().now()) is InviteStatus.ACTIVE


def test_end_to_end_signup_over_http_on_postgres(pg_url: str) -> None:
    # Seed a committed admin invite, then drive the real SQL-backed app over HTTP.
    # Schema + seed run in their own engine/loop so nothing is shared with the
    # app's engine, which binds to the TestClient's event loop.
    async def seed() -> str:
        eng = create_engine(pg_url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        sm = create_sessionmaker(eng)
        async with sm() as s, s.begin():
            created = await InviteService(SqlInviteRepository(s), SystemClock()).create(
                created_by="seed", role=Role.ADMIN
            )
        await eng.dispose()
        return created.raw_token

    token = asyncio.run(seed())
    app = create_app(
        Settings(database_url=pg_url, environment="test", session_cookie_secure=False)
    )
    with TestClient(app) as client:
        signup = client.post(
            "/auth/signup",
            json={"token": token, "email": "admin@example.com", "password": PASSWORD},
        )
        assert signup.status_code == 201
        assert signup.json()["role"] == "admin"
        me = client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "admin@example.com"


# --- M1 aggregates on Postgres ---


def _mk_deck(deck_id: str = "d1", owner: str = "alice", parent: str | None = None) -> Deck:
    return Deck(id=deck_id, owner_id=owner, name="Deck", parent_id=parent, created_at=_now())


async def test_deck_sql_crud(engine: AsyncEngine) -> None:
    from flashcards.domain.decks import Deck

    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        await SqlDeckRepository(s).add(_mk_deck())
    async with sm() as s, s.begin():
        repo = SqlDeckRepository(s)
        assert (await repo.get("d1")) is not None
        await repo.update(
            Deck(id="d1", owner_id="alice", name="Renamed", parent_id=None, created_at=_now())
        )
    async with sm() as s:
        repo = SqlDeckRepository(s)
        renamed = await repo.get("d1")
        assert renamed is not None and renamed.name == "Renamed"
        assert [d.id for d in await repo.list_for_owner("alice")] == ["d1"]
        assert await repo.list_for_owner("bob") == []


async def test_note_and_card_sql_roundtrip(engine: AsyncEngine) -> None:
    from flashcards.domain.notes import Card, Note, NoteType

    sm = create_sessionmaker(engine)
    note = Note(
        id="n1",
        owner_id="alice",
        deck_id="d1",
        note_type=NoteType.CLOZE,
        fields=(("text", "{{c1::a}} {{c2::b}}"),),
        created_at=_now(),
    )
    async with sm() as s, s.begin():
        await SqlDeckRepository(s).add(_mk_deck())
        await SqlNoteRepository(s).add(note)
        for ordinal in (1, 2):
            await SqlCardRepository(s).add(
                Card(
                    id=f"c{ordinal}",
                    owner_id="alice",
                    deck_id="d1",
                    note_id="n1",
                    ordinal=ordinal,
                    created_at=_now(),
                )
            )
    async with sm() as s:
        fetched = await SqlNoteRepository(s).get("n1")
        assert fetched is not None and fetched.field("text") == "{{c1::a}} {{c2::b}}"
        assert len(await SqlCardRepository(s).list_for_deck("d1")) == 2
    async with sm() as s, s.begin():
        await SqlCardRepository(s).delete_for_note("n1")
    async with sm() as s:
        assert await SqlCardRepository(s).list_for_deck("d1") == []


async def test_schedule_sql_upsert(engine: AsyncEngine) -> None:
    from flashcards.domain.scheduling import CardSchedule, ScheduleState

    sm = create_sessionmaker(engine)
    # A schedule row needs its card (FK), so seed deck + note + card first.
    from flashcards.domain.notes import Card, Note, NoteType

    async with sm() as s, s.begin():
        await SqlDeckRepository(s).add(_mk_deck())
        await SqlNoteRepository(s).add(
            Note(
                id="n1",
                owner_id="alice",
                deck_id="d1",
                note_type=NoteType.BASIC,
                fields=(("front", "Q"), ("back", "A")),
                created_at=_now(),
            )
        )
        await SqlCardRepository(s).add(
            Card(
                id="c1",
                owner_id="alice",
                deck_id="d1",
                note_id="n1",
                ordinal=0,
                created_at=_now(),
            )
        )
    async with sm() as s, s.begin():
        repo = SqlScheduleRepository(s)
        assert await repo.get("c1") is None
        await repo.upsert(
            CardSchedule(
                card_id="c1",
                state=ScheduleState.LEARNING,
                step=0,
                stability=None,
                difficulty=None,
                due_at=_now(),
                last_reviewed_at=None,
            )
        )
    async with sm() as s, s.begin():
        await SqlScheduleRepository(s).upsert(
            CardSchedule(
                card_id="c1",
                state=ScheduleState.REVIEW,
                step=None,
                stability=5.0,
                difficulty=5.0,
                due_at=_now(),
                last_reviewed_at=_now(),
            )
        )
    async with sm() as s:
        got = await SqlScheduleRepository(s).get("c1")
        assert got is not None and got.state is ScheduleState.REVIEW


async def _seed_card(sm: async_sessionmaker[AsyncSession]) -> None:
    from flashcards.domain.notes import Card, Note, NoteType

    async with sm() as s, s.begin():
        await SqlDeckRepository(s).add(_mk_deck())
        await SqlNoteRepository(s).add(
            Note(
                id="n1",
                owner_id="alice",
                deck_id="d1",
                note_type=NoteType.BASIC,
                fields=(("front", "Q"), ("back", "A")),
                created_at=_now(),
            )
        )
        for cid in ("c1", "c2"):
            await SqlCardRepository(s).add(
                Card(
                    id=cid,
                    owner_id="alice",
                    deck_id="d1",
                    note_id="n1",
                    ordinal=0,
                    created_at=_now(),
                )
            )


async def test_reviewlog_sql_duplicate_key_is_rejected(engine: AsyncEngine) -> None:
    from flashcards.domain.scheduling import DuplicateReview, Rating, ReviewLog

    sm = create_sessionmaker(engine)
    await _seed_card(sm)

    def log(key: str | None, card: str = "c1", owner: str = "alice") -> ReviewLog:
        return ReviewLog(
            id=str(uuid.uuid4()),
            owner_id=owner,
            card_id=card,
            rating=Rating.GOOD,
            reviewed_at=_now(),
            idempotency_key=key,
        )

    async with sm() as s, s.begin():
        repo = SqlReviewLogRepository(s)
        await repo.append(log("k1"))
        with pytest.raises(DuplicateReview):
            await repo.append(log("k1"))
        # Same key, different card is fine; keyless appends never dedup.
        await repo.append(log("k1", card="c2"))
        await repo.append(log(None))
        await repo.append(log(None))


async def test_deck_delete_cascades_to_notes_cards_schedule_logs(engine: AsyncEngine) -> None:
    from flashcards.domain.scheduling import CardSchedule, Rating, ReviewLog, ScheduleState

    sm = create_sessionmaker(engine)
    await _seed_card(sm)
    async with sm() as s, s.begin():
        await SqlScheduleRepository(s).upsert(
            CardSchedule(
                card_id="c1",
                state=ScheduleState.LEARNING,
                step=0,
                stability=None,
                difficulty=None,
                due_at=_now(),
                last_reviewed_at=None,
            )
        )
        await SqlReviewLogRepository(s).append(
            ReviewLog(
                id="l1",
                owner_id="alice",
                card_id="c1",
                rating=Rating.GOOD,
                reviewed_at=_now(),
                idempotency_key="k",
            )
        )
    async with sm() as s, s.begin():
        await SqlDeckRepository(s).delete("d1")
    async with sm() as s:
        assert await SqlNoteRepository(s).get("n1") is None
        assert await SqlCardRepository(s).get("c1") is None
        assert await SqlScheduleRepository(s).get("c1") is None


async def test_sql_card_update_note_delete_and_missing_deck_delete(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    await _seed_card(sm)
    async with sm() as s, s.begin():
        card_repo = SqlCardRepository(s)
        c1 = await card_repo.get("c1")
        assert c1 is not None
        await card_repo.update(c1.suspended(_now()))
    async with sm() as s:
        updated = await SqlCardRepository(s).get("c1")
        assert updated is not None and updated.is_suspended
    async with sm() as s, s.begin():
        await SqlNoteRepository(s).delete("n1")
    async with sm() as s:
        assert await SqlNoteRepository(s).get("n1") is None
    # Deleting rows that do not exist is a no-op.
    async with sm() as s, s.begin():
        await SqlDeckRepository(s).delete("does-not-exist")
        await SqlNoteRepository(s).delete("does-not-exist")


async def test_sql_library_delete_deck_cascades(engine: AsyncEngine) -> None:
    from flashcards.application.deck_service import DeckService
    from flashcards.application.library_service import LibraryService
    from flashcards.application.note_service import NoteService
    from flashcards.domain.scheduling import CardSchedule, Rating, ReviewLog, ScheduleState

    sm = create_sessionmaker(engine)
    await _seed_card(sm)
    async with sm() as s, s.begin():
        await SqlScheduleRepository(s).upsert(
            CardSchedule(
                card_id="c1",
                state=ScheduleState.LEARNING,
                step=0,
                stability=None,
                difficulty=None,
                due_at=_now(),
                last_reviewed_at=None,
            )
        )
        await SqlReviewLogRepository(s).append(
            ReviewLog(
                id="l1",
                owner_id="alice",
                card_id="c1",
                rating=Rating.GOOD,
                reviewed_at=_now(),
                idempotency_key=None,
            )
        )
    async with sm() as s, s.begin():
        decks = DeckService(SqlDeckRepository(s), SystemClock())
        notes = NoteService(decks, SqlNoteRepository(s), SqlCardRepository(s), SystemClock())
        library = LibraryService(
            decks,
            notes,
            SqlDeckRepository(s),
            SqlNoteRepository(s),
            SqlCardRepository(s),
            SqlScheduleRepository(s),
            SqlReviewLogRepository(s),
        )
        await library.delete_deck(owner_id="alice", deck_id="d1")
    async with sm() as s:
        assert await SqlDeckRepository(s).get("d1") is None
        assert await SqlNoteRepository(s).get("n1") is None
        assert await SqlCardRepository(s).get("c1") is None
        assert await SqlScheduleRepository(s).get("c1") is None
