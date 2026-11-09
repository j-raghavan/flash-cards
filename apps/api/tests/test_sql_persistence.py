# SPDX-License-Identifier: AGPL-3.0-only
"""Integration tests for the SQLAlchemy adapters against a real Postgres.

These require Docker: a throwaway Postgres container is started per test session.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
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
from flashcards.adapters.mailers import LogMailer
from flashcards.adapters.media import PuremagicMediaSniffer
from flashcards.adapters.memory import InMemoryJobQueue, SystemClock
from flashcards.adapters.passwords import Argon2PasswordHasher
from flashcards.adapters.sql import (
    SqlAiJobRepository,
    SqlAuditRepository,
    SqlCardRepository,
    SqlDeckRepository,
    SqlDraftCardRepository,
    SqlGoalRepository,
    SqlInviteRepository,
    SqlLoginThrottleRepository,
    SqlMediaRepository,
    SqlMockExamRepository,
    SqlNoteRepository,
    SqlNoteTypeRepository,
    SqlObjectStore,
    SqlPassageRepository,
    SqlPlanBaselineRepository,
    SqlQuotaRepository,
    SqlReviewLogRepository,
    SqlScheduleRepository,
    SqlSessionRepository,
    SqlShareLinkRepository,
    SqlStudySettingsRepository,
    SqlStudyStatsRepository,
    SqlUserRepository,
    SqlUserTokenRepository,
)
from flashcards.adapters.transcripts import NullTranscriptProvider
from flashcards.api.app import create_app
from flashcards.api.container import Repositories, build_services
from flashcards.application.invite_service import InviteService
from flashcards.config import Settings
from flashcards.domain.decks import Deck
from flashcards.domain.invites import InviteStatus, Role
from flashcards.domain.note_types import CardTemplate, CustomNoteType
from flashcards.domain.ports import PasswordHasher
from flashcards.domain.sessions import Session
from flashcards.domain.sharing import ShareLink
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
        note_types=SqlNoteTypeRepository(session),
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
        mock_exams=SqlMockExamRepository(session),
        transcripts=NullTranscriptProvider(),
        passages=SqlPassageRepository(session),
        ai_calls=SqlQuotaRepository(session, limit=200),
        share_links=SqlShareLinkRepository(session),
        clone_quota=SqlQuotaRepository(session, limit=50_000),
        audit_events=SqlAuditRepository(session),
        login_throttle=SqlLoginThrottleRepository(session),
        user_tokens=SqlUserTokenRepository(session),
        mailer=LogMailer(show_content=False),
        study_settings=SqlStudySettingsRepository(session),
        plan_baselines=SqlPlanBaselineRepository(session),
        study_stats=SqlStudyStatsRepository(session),
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

        # Failed sign-ins must persist across requests on Postgres: each 401 commits
        # its count, so the sixth attempt is locked even with the right password.
        for _ in range(5):
            wrong = {"email": "admin@example.com", "password": "not-the-password"}
            assert client.post("/auth/login", json=wrong).status_code == 401
        right = {"email": "admin@example.com", "password": PASSWORD}
        locked = client.post("/auth/login", json=right)
        assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0


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
        for ordinal, cid in enumerate(("c1", "c2")):
            await SqlCardRepository(s).add(
                Card(
                    id=cid,
                    owner_id="alice",
                    deck_id="d1",
                    note_id="n1",
                    ordinal=ordinal,  # one card per ordinal (uq_cards_note_ordinal)
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
        # The deck's share links go with it.
        assert await SqlShareLinkRepository(s).get("share1") is None
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
        await SqlShareLinkRepository(s).add(
            ShareLink(
                id="share1", owner_id="alice", deck_id="d1", token_hash="share-hash",
                allow_clone=True, created_at=_now(),
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
            SqlPassageRepository(s),
            SqlMockExamRepository(s),
            SqlShareLinkRepository(s),
        )
        await library.delete_deck(owner_id="alice", deck_id="d1")
    async with sm() as s:
        assert await SqlDeckRepository(s).get("d1") is None
        assert await SqlNoteRepository(s).get("n1") is None
        assert await SqlCardRepository(s).get("c1") is None
        assert await SqlScheduleRepository(s).get("c1") is None


# --- Custom note types and image occlusion on Postgres (ADR 0013) ---


def _vocab_type(type_id: str = "t1", owner: str = "alice") -> CustomNoteType:
    return CustomNoteType(
        id=type_id,
        owner_id=owner,
        name="Vocab",
        fields=("Word", "Meaning"),
        templates=(
            CardTemplate(ordinal=0, name="Forward", front="{{Word}}", back="{{Meaning}}"),
            CardTemplate(ordinal=2, name="Reverse", front="{{Meaning}}", back="{{Word}}"),
        ),
        created_at=_now(),
    )


async def test_note_type_sql_repository(engine: AsyncEngine) -> None:
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        repo = SqlNoteTypeRepository(s)
        await repo.add(_vocab_type())
        await repo.add(_vocab_type("t2"))
        await repo.add(_vocab_type("t3", owner="bob"))
    async with sm() as s, s.begin():
        repo = SqlNoteTypeRepository(s)
        fetched = await repo.get("t1")
        assert fetched is not None
        expected = _vocab_type()
        assert replace(fetched, created_at=expected.created_at) == expected
        assert await repo.get("missing") is None
        assert await repo.lock("missing") is None
        locked = await repo.lock("t1")
        assert locked is not None and locked.templates[1].ordinal == 2
        assert [t.id for t in await repo.list_for_owner("alice")] == ["t1", "t2"]
        assert await repo.count_for_owner("alice") == 2
        await repo.update(replace(locked, name="Words", fields=("Word",)))
        await repo.update(_vocab_type("never-added"))  # a missing type is left alone
    async with sm() as s, s.begin():
        repo = SqlNoteTypeRepository(s)
        renamed = await repo.lock("t1")
        assert renamed is not None and renamed.name == "Words" and renamed.fields == ("Word",)
        assert await repo.get("never-added") is None
        await repo.delete("t2")
        assert await repo.get("t2") is None
        await repo.delete_for_owner("alice")
    async with sm() as s:
        repo = SqlNoteTypeRepository(s)
        assert await repo.list_for_owner("alice") == []
        assert [t.id for t in await repo.list_for_owner("bob")] == ["t3"]


async def test_notes_keep_their_type_and_masks_on_postgres(engine: AsyncEngine) -> None:
    from flashcards.domain.notes import Mask, Note, NoteType, Occlusion

    sm = create_sessionmaker(engine)
    custom = Note(
        id="n1", owner_id="alice", deck_id="d1", note_type=NoteType.CUSTOM,
        fields=(("Word", "chat"), ("Meaning", "cat")), created_at=_now(), note_type_id="t1",
    )
    occluded = Note(
        id="n2", owner_id="alice", deck_id="d1", note_type=NoteType.IMAGE_OCCLUSION,
        fields=(("header", "Heart"),), created_at=_now(),
        occlusion=Occlusion(image_id="img", masks=(Mask(1, 0.1, 0.2, 0.3, 0.4),)),
    )
    async with sm() as s, s.begin():
        await SqlDeckRepository(s).add(_mk_deck())
        await SqlNoteTypeRepository(s).add(_vocab_type())
        await SqlNoteRepository(s).add(custom)
        await SqlNoteRepository(s).add(occluded)
    async with sm() as s:
        notes = SqlNoteRepository(s)
        fetched_custom, fetched_occluded = await notes.get("n1"), await notes.get("n2")
        assert fetched_custom is not None and fetched_occluded is not None
        assert fetched_custom.fields == custom.fields  # field order survives the JSON column
        assert fetched_custom.note_type_id == "t1" and fetched_custom.occlusion is None
        assert fetched_occluded.occlusion == occluded.occlusion
        assert fetched_occluded.note_type_id is None
        assert [n.id for n in await notes.list_for_note_type("t1")] == ["n1"]
        assert await notes.count_for_note_type("t1") == 1
        assert await notes.count_for_note_type("t2") == 0
    # A type in use cannot be deleted underneath its notes.
    with pytest.raises(IntegrityError):
        async with sm() as s, s.begin():
            await SqlNoteTypeRepository(s).delete("t1")


def test_note_types_and_occlusion_over_http_on_postgres(pg_url: str) -> None:
    async def seed() -> list[str]:
        eng = create_engine(pg_url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        sm = create_sessionmaker(eng)
        tokens: list[str] = []
        async with sm() as s, s.begin():
            for _ in range(2):
                created = await InviteService(SqlInviteRepository(s), SystemClock()).create(
                    created_by="seed", role=Role.MEMBER
                )
                tokens.append(created.raw_token)
        await eng.dispose()
        return tokens

    alice_token, bob_token = asyncio.run(seed())
    app = create_app(
        Settings(database_url=pg_url, environment="test", session_cookie_secure=False)
    )
    # One client (one event loop for the app's engine); the two users take turns.
    with TestClient(app) as alice:
        signup = alice.post(
            "/auth/signup",
            json={"token": alice_token, "email": "alice@example.com", "password": PASSWORD},
        )
        assert signup.status_code == 201
        vocab = {
            "name": "Vocab",
            "fields": ["Word", "Meaning"],
            "templates": [
                {"name": "Forward", "front": "{{Word}}", "back": "{{Meaning}}"},
                {"name": "Reverse", "front": "{{Meaning}}", "back": "{{Word}}"},
            ],
        }
        type_id = alice.post("/note-types", json=vocab).json()["id"]
        deck_id = alice.post("/decks", json={"name": "Lang"}).json()["id"]
        cards = alice.post(
            f"/decks/{deck_id}/notes",
            json={
                "note_type": "custom",
                "note_type_id": type_id,
                "fields": {"Word": "chat", "Meaning": "cat"},
            },
        ).json()
        assert [c["ordinal"] for c in cards] == [0, 1]
        reviewed = alice.post(f"/cards/{cards[0]['id']}/review", json={"rating": 3})
        assert reviewed.status_code == 200

        edited = alice.patch(
            f"/note-types/{type_id}",
            json={
                "fields": ["Term", "Meaning"],
                "renamed": {"Word": "Term"},
                "templates": [{"ordinal": 0, "name": "Forward", "front": "{{Term}}", "back": ""}],
            },
        )
        assert edited.status_code == 200
        note = alice.get(f"/notes/{cards[0]['note_id']}").json()
        assert note["fields"] == {"Term": "chat", "Meaning": "cat"}
        assert [c["id"] for c in note["cards"]] == [cards[0]["id"]]
        assert alice.delete(f"/note-types/{type_id}").status_code == 409

        image_id = alice.post(
            "/media", files={"file": ("x.png", _tiny_png(), "image/png")}
        ).json()["id"]
        masks = [{"id": 1, "left": 0.1, "top": 0.1, "width": 0.2, "height": 0.2}]
        occluded = alice.post(
            f"/decks/{deck_id}/notes",
            json={
                "note_type": "image_occlusion",
                "fields": {},
                "occlusion": {"image_id": image_id, "masks": masks},
            },
        )
        assert occluded.status_code == 201
        detail = alice.get(f"/cards/{occluded.json()[0]['id']}").json()
        assert detail["occlusion"] == {"image_id": image_id, "masks": masks}

        share = alice.post(f"/decks/{deck_id}/shares", json={"allow_clone": True})
        share_token = share.json()["token"]

        alice.cookies.clear()  # now Bob
        bob = alice
        signup = bob.post(
            "/auth/signup",
            json={"token": bob_token, "email": "bob@example.com", "password": PASSWORD},
        )
        assert signup.status_code == 201
        assert bob.post(f"/shared/{share_token}/clone").status_code == 201
        assert len(bob.get("/note-types").json()) == 1
        assert len(bob.get("/account/export").json()["media"]) == 1
        assert bob.delete("/account").status_code == 204

        login = {"email": "alice@example.com", "password": PASSWORD}
        assert alice.post("/auth/login", json=login).status_code == 200
        assert alice.delete("/account").status_code == 204


def _tiny_png() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=16, height=16)
    try:
        return bytes(page.get_pixmap().tobytes("png"))
    finally:
        doc.close()


async def test_locking_notes_returns_the_committed_row_not_a_cached_one(
    engine: AsyncEngine,
) -> None:
    from flashcards.domain.notes import Note, NoteType

    sm = create_sessionmaker(engine)
    note = Note(
        id="n1", owner_id="alice", deck_id="d1", note_type=NoteType.BASIC,
        fields=(("front", "old"), ("back", "a")), created_at=_now(),
    )
    async with sm() as s, s.begin():
        await SqlDeckRepository(s).add(_mk_deck())
        await SqlNoteRepository(s).add(note)
    async with sm() as reader, reader.begin():
        assert await SqlNoteRepository(reader).get("n1") == note  # cached in this session
        async with sm() as writer, writer.begin():
            await SqlNoteRepository(writer).update(note.with_fields((("front", "new"),)))
        locked = await SqlNoteRepository(reader).lock_many(["n1"])
        assert locked[0].fields == (("front", "new"),)


async def test_sql_apkg_round_trip_keeps_history_and_schedule(engine: AsyncEngine) -> None:
    from flashcards.domain.deck_export import ExportFormat
    from flashcards.domain.notes import NoteType
    from flashcards.domain.scheduling import Rating

    sm = create_sessionmaker(engine)
    hasher = Argon2PasswordHasher()
    async with sm() as s, s.begin():
        services = build_services(_sql_repositories(s, hasher))
        root = await services.decks.create(owner_id="alice", name="Spanish")
        child = await services.decks.create(owner_id="alice", name="Verbs", parent_id=root.id)
        _note, (card,) = await services.notes.create_note(
            owner_id="alice",
            deck_id=child.id,
            note_type=NoteType.BASIC,
            fields={"front": "ser", "back": "to be"},
            tags=["verbs"],
        )
        await services.reviews.submit(owner_id="alice", card_id=card.id, rating=Rating.GOOD)
        await services.notes.suspend_card(owner_id="alice", card_id=card.id)
    async with sm() as s, s.begin():
        services = build_services(_sql_repositories(s, hasher))
        exported = await services.deck_export.export(
            owner_id="alice", deck_id=root.id, export_format=ExportFormat.APKG
        )
        dry = await services.apkg_import.import_package(
            owner_id="bob", data=exported.data, dry_run=True
        )
        assert await services.decks.list_decks("bob") == []  # a dry run writes nothing
        report = await services.apkg_import.import_package(owner_id="bob", data=exported.data)
    assert dry.notes_imported == report.notes_imported == 1
    assert (report.decks_created, report.reviews_imported, report.cards_scheduled) == (2, 1, 1)
    async with sm() as s:
        bob_cards = await SqlCardRepository(s).list_for_owner("bob")
        assert len(bob_cards) == 1 and bob_cards[0].is_suspended
        assert await SqlScheduleRepository(s).get(bob_cards[0].id) is not None
        logs = await SqlReviewLogRepository(s).list_for_owner("bob")
        assert [log.rating for log in logs] == [Rating.GOOD]
        bob_decks = await SqlDeckRepository(s).list_for_owner("bob")
        assert sorted(deck.name for deck in bob_decks) == ["Spanish", "Verbs"]
