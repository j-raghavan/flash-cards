# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from flashcards.adapters.memory import (
    InMemoryAiJobRepository,
    InMemoryCardRepository,
    InMemoryDeckRepository,
    InMemoryDraftCardRepository,
    InMemoryGoalRepository,
    InMemoryLoginThrottleRepository,
    InMemoryMediaRepository,
    InMemoryMockExamRepository,
    InMemoryNoteRepository,
    InMemoryObjectStore,
    InMemoryPassageRepository,
    InMemoryQuotaRepository,
    InMemoryReviewLogRepository,
    InMemoryScheduleRepository,
    InMemorySessionRepository,
    InMemoryShareLinkRepository,
    InMemoryUserRepository,
    InMemoryUserTokenRepository,
)
from flashcards.application.account_service import AccountService
from flashcards.application.deck_service import DeckService
from flashcards.application.goal_service import GoalService
from flashcards.application.library_service import LibraryService
from flashcards.application.note_service import NoteService
from flashcards.application.sign_in_service import throttle_key
from flashcards.domain.ai import DraftCardRecord, Thoroughness
from flashcards.domain.chat import Passage
from flashcards.domain.invites import Role
from flashcards.domain.jobs import new_generation_job
from flashcards.domain.media import MediaAsset
from flashcards.domain.mock_exam import ExamQuestion, MockExam
from flashcards.domain.notes import NoteType
from flashcards.domain.scheduling import CardSchedule, Rating, ReviewLog, ScheduleState
from flashcards.domain.sessions import Session
from flashcards.domain.sharing import ShareLink
from flashcards.domain.user_tokens import TokenPurpose, UserToken
from flashcards.domain.users import User

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ALICE = User(
    id="alice", email="alice@example.com", password_hash="h", role=Role.MEMBER, created_at=NOW
)
BOB = "bob"


class FakeClock:
    def now(self) -> datetime:
        return NOW


class _Fixture:
    def __init__(self) -> None:
        clock = FakeClock()
        self.deck_repo = InMemoryDeckRepository()
        self.note_repo = InMemoryNoteRepository()
        self.card_repo = InMemoryCardRepository()
        self.schedule_repo = InMemoryScheduleRepository()
        self.log_repo = InMemoryReviewLogRepository()
        self.goal_repo = InMemoryGoalRepository()
        self.ai_jobs = InMemoryAiJobRepository()
        self.draft_repo = InMemoryDraftCardRepository()
        self.store = InMemoryObjectStore()
        self.sessions = InMemorySessionRepository()
        self.users = InMemoryUserRepository()
        self.media = InMemoryMediaRepository()
        self.quotas = InMemoryQuotaRepository()
        self.mock_exams = InMemoryMockExamRepository()
        self.passages = InMemoryPassageRepository()
        self.ai_calls = InMemoryQuotaRepository(limit=5)
        self.share_links = InMemoryShareLinkRepository()
        self.clone_quota = InMemoryQuotaRepository(limit=100)
        self.throttle = InMemoryLoginThrottleRepository()
        self.user_tokens = InMemoryUserTokenRepository()
        self.decks = DeckService(self.deck_repo, clock)
        self.notes = NoteService(self.decks, self.note_repo, self.card_repo, clock)
        self.goals = GoalService(self.decks, self.goal_repo, clock)
        library = LibraryService(
            self.decks, self.notes, self.deck_repo, self.note_repo,
            self.card_repo, self.schedule_repo, self.log_repo, self.passages,
            self.mock_exams, self.share_links,
        )
        self.service = AccountService(
            library, self.deck_repo, self.note_repo, self.card_repo, self.schedule_repo,
            self.log_repo, self.goal_repo, self.ai_jobs, self.draft_repo, self.store,
            self.sessions, self.users, self.media, self.quotas, self.mock_exams,
            self.passages, self.ai_calls, self.share_links, self.clone_quota, self.throttle,
            self.user_tokens,
        )

    async def seed_full_account(self, owner: str) -> None:
        if owner == ALICE.id:
            await self.users.add(ALICE)
        deck = await self.decks.create(owner_id=owner, name=f"{owner}-deck")
        self._deck_id = deck.id
        _n, cards = await self.notes.create_note(
            owner_id=owner, deck_id=deck.id, note_type=NoteType.BASIC,
            fields={"front": "q", "back": "a"},
        )
        # A second card that is never reviewed, so it has no schedule.
        await self.notes.create_note(
            owner_id=owner, deck_id=deck.id, note_type=NoteType.BASIC,
            fields={"front": "new", "back": "card"},
        )
        await self.schedule_repo.upsert(
            CardSchedule(
                card_id=cards[0].id, state=ScheduleState.REVIEW, step=None, stability=5.0,
                difficulty=5.0, due_at=NOW, last_reviewed_at=NOW,
            )
        )
        await self.log_repo.append(
            ReviewLog(
                id=str(uuid.uuid4()), owner_id=owner, card_id=cards[0].id,
                rating=Rating.GOOD, reviewed_at=NOW,
            )
        )
        await self.goals.create(
            owner_id=owner, name="Goal", deck_ids=[deck.id],
            exam_date=NOW + timedelta(days=30), desired_grade=0.5,
        )
        job = new_generation_job(
            owner_id=owner, source_id=f"src-{owner}", draft_deck_id=deck.id,
            thoroughness=Thoroughness.STANDARD, now=NOW,
        )
        await self.ai_jobs.add(job)
        await self.store.put(job.source_id, b"pdf", "application/pdf")
        await self.draft_repo.add(
            DraftCardRecord(
                id=str(uuid.uuid4()), job_id=job.id, owner_id=owner, note_type=NoteType.BASIC,
                fields=(("front", "q"), ("back", "a")), source_page=1, created_at=NOW,
            )
        )
        self._job_id = job.id
        self._source = job.source_id
        await self.media.add(
            MediaAsset(
                id=f"media-{owner}", owner_id=owner, content_type="image/png",
                data=b"\x89PNG", created_at=NOW,
            )
        )
        await self.mock_exams.add(
            MockExam(
                id=f"exam-{owner}", owner_id=owner, deck_id=deck.id, created_at=NOW,
                time_limit_seconds=None,
                questions=(ExamQuestion(id="q", prompt="p", reference="r", topic="t"),),
            )
        )
        await self.passages.add_many(
            [
                Passage(
                    id=f"passage-{owner}", owner_id=owner, deck_id=deck.id, file_id="f",
                    page=1, ordinal=0, text="indexed source text",
                )
            ]
        )
        await self.quotas.try_reserve_pages(owner, 1, "2026-11")
        await self.ai_calls.try_reserve_pages(owner, 5, "calls:2026-11-08")
        await self.share_links.add(
            ShareLink(
                id=f"share-{owner}", owner_id=owner, deck_id=deck.id, token_hash=f"h-{owner}",
                allow_clone=False, created_at=NOW,
            )
        )


async def test_export_gathers_everything_the_user_owns() -> None:
    fx = _Fixture()
    await fx.seed_full_account(ALICE.id)
    await fx.decks.create(owner_id=BOB, name="Bob's deck")  # noise

    export = await fx.service.export(user=ALICE)

    assert [d.name for d in export.decks] == ["alice-deck"]
    assert len(export.notes) == 2 and len(export.cards) == 2
    assert len(export.schedules) == 1  # only the reviewed card has one
    assert len(export.review_logs) == 1
    assert len(export.goals) == 1
    assert [m.id for m in export.media] == ["media-alice"]
    assert [e.id for e in export.mock_exams] == ["exam-alice"]
    assert [s.id for s in export.share_links] == ["share-alice"]


async def test_delete_removes_everything_the_user_owns_and_nothing_else() -> None:
    fx = _Fixture()
    await fx.seed_full_account(ALICE.id)
    alice_job, alice_source, fx_alice_deck = fx._job_id, fx._source, fx._deck_id
    # A nested deck, so deletion walks the subtree (and skips the child in the loop).
    await fx.decks.create(owner_id=ALICE.id, name="child", parent_id=fx._deck_id)
    await fx.sessions.add(
        Session(
            id="s1", user_id=ALICE.id, token_hash="th", created_at=NOW,
            expires_at=NOW + timedelta(days=1), revoked_at=None,
        )
    )
    await fx.seed_full_account(BOB)  # Bob's full account must survive Alice's deletion

    await fx.throttle.register_attempt(throttle_key(ALICE.email), NOW)
    await fx.user_tokens.add(
        UserToken(
            id="tok", user_id=ALICE.id, purpose=TokenPurpose.PASSWORD_RESET, token_hash="h",
            created_at=NOW, expires_at=NOW + timedelta(hours=1),
        )
    )
    await fx.service.delete(user=ALICE)
    await fx.users.delete("ghost")  # deleting a missing user is a no-op

    assert await fx.users.get(ALICE.id) is None
    assert await fx.sessions.list_for_user(ALICE.id) == []
    assert await fx.decks.list_decks(owner_id=ALICE.id) == []
    assert await fx.goal_repo.list_for_owner(ALICE.id) == []
    assert await fx.ai_jobs.list_for_owner(ALICE.id) == []
    assert await fx.draft_repo.list_for_job(alice_job) == []
    assert await fx.store.get(alice_source) is None
    assert await fx.log_repo.list_for_owner(ALICE.id) == []
    assert await fx.media.list_for_owner(ALICE.id) == []
    assert await fx.mock_exams.get("exam-alice") is None
    assert await fx.share_links.get("share-alice") is None
    assert await fx.throttle.get(throttle_key(ALICE.email)) is None
    assert await fx.user_tokens.latest_for_user(ALICE.id, TokenPurpose.PASSWORD_RESET) is None
    assert await fx.share_links.get("share-bob") is not None
    assert await fx.passages.indexed_files(fx_alice_deck) == set()
    assert await fx.quotas.try_reserve_pages(ALICE.id, 1, "2026-11") is True  # counter reset
    assert await fx.ai_calls.try_reserve_pages(ALICE.id, 1, "calls:2026-11-08") is True
    # Bob keeps his deck, his media and his mock exam.
    assert len(await fx.decks.list_decks(owner_id=BOB)) == 1
    assert [m.id for m in await fx.media.list_for_owner(BOB)] == ["media-bob"]
    assert await fx.mock_exams.get("exam-bob") is not None
    assert await fx.passages.indexed_files(fx._deck_id) == {"f"}  # Bob's, seeded last
