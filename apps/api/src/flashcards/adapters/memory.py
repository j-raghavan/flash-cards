# SPDX-License-Identifier: AGPL-3.0-only
"""In-memory adapters for tests and local experiments. Not for production.

The methods are async to satisfy the repository ports. Each runs to completion
without awaiting, so on a single event loop it is effectively atomic, which is
what the invite single-use guarantee relies on here.
"""

from __future__ import annotations

from datetime import UTC, datetime

from flashcards.domain.ai import DraftCardRecord
from flashcards.domain.chat import Passage
from flashcards.domain.decks import Deck
from flashcards.domain.goals import Goal
from flashcards.domain.invites import Invite
from flashcards.domain.jobs import AiJob, JobStatus
from flashcards.domain.media import MediaAsset
from flashcards.domain.mock_exam import MockExam
from flashcards.domain.notes import Card, Note
from flashcards.domain.quotas import DEFAULT_MONTHLY_PAGES
from flashcards.domain.scheduling import CardSchedule, DuplicateReview, ReviewLog
from flashcards.domain.sessions import Session
from flashcards.domain.users import EmailAlreadyRegistered, User


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class InMemoryInviteRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, Invite] = {}

    async def add(self, invite: Invite) -> None:
        self._by_id[invite.id] = invite

    async def get(self, invite_id: str) -> Invite | None:
        return self._by_id.get(invite_id)

    async def get_by_token_hash(self, token_hash: str) -> Invite | None:
        for invite in self._by_id.values():
            if invite.token_hash == token_hash:
                return invite
        return None

    async def list_all(self) -> list[Invite]:
        return sorted(self._by_id.values(), key=lambda inv: inv.created_at, reverse=True)

    async def try_redeem(self, invite_id: str, now: datetime) -> bool:
        invite = self._by_id.get(invite_id)
        if invite is None or invite.redeemed_at is not None or invite.revoked_at is not None:
            return False
        self._by_id[invite_id] = invite.with_redeemed(now)
        return True

    async def try_revoke(self, invite_id: str, now: datetime) -> bool:
        invite = self._by_id.get(invite_id)
        if invite is None or invite.redeemed_at is not None or invite.revoked_at is not None:
            return False
        self._by_id[invite_id] = invite.with_revoked(now)
        return True


class InMemoryUserRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, User] = {}
        self._email_to_id: dict[str, str] = {}

    async def add(self, user: User) -> None:
        if user.email in self._email_to_id:
            raise EmailAlreadyRegistered(user.email)
        self._by_id[user.id] = user
        self._email_to_id[user.email] = user.id

    async def get(self, user_id: str) -> User | None:
        return self._by_id.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        user_id = self._email_to_id.get(email)
        return self._by_id.get(user_id) if user_id is not None else None

    async def delete(self, user_id: str) -> None:
        user = self._by_id.pop(user_id, None)
        if user is not None:
            self._email_to_id.pop(user.email, None)


class InMemorySessionRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, Session] = {}

    async def add(self, session: Session) -> None:
        self._by_id[session.id] = session

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        for session in self._by_id.values():
            if session.token_hash == token_hash:
                return session
        return None

    async def list_for_user(self, user_id: str) -> list[Session]:
        sessions = [s for s in self._by_id.values() if s.user_id == user_id]
        return sorted(sessions, key=lambda s: s.created_at, reverse=True)

    async def revoke(self, session_id: str, now: datetime) -> bool:
        session = self._by_id.get(session_id)
        if session is None or session.revoked_at is not None:
            return False
        self._by_id[session_id] = session.with_revoked(now)
        return True

    async def delete_for_user(self, user_id: str) -> None:
        for session_id in [s.id for s in self._by_id.values() if s.user_id == user_id]:
            del self._by_id[session_id]


class InMemoryDeckRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, Deck] = {}

    async def add(self, deck: Deck) -> None:
        self._by_id[deck.id] = deck

    async def get(self, deck_id: str) -> Deck | None:
        return self._by_id.get(deck_id)

    async def list_for_owner(self, owner_id: str) -> list[Deck]:
        owned = [d for d in self._by_id.values() if d.owner_id == owner_id]
        return sorted(owned, key=lambda d: d.created_at)

    async def update(self, deck: Deck) -> None:
        self._by_id[deck.id] = deck

    async def delete(self, deck_id: str) -> None:
        self._by_id.pop(deck_id, None)


class InMemoryNoteRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, Note] = {}

    async def add(self, note: Note) -> None:
        self._by_id[note.id] = note

    async def get(self, note_id: str) -> Note | None:
        return self._by_id.get(note_id)

    async def list_for_deck(self, deck_id: str) -> list[Note]:
        notes = [note for note in self._by_id.values() if note.deck_id == deck_id]
        return sorted(notes, key=lambda note: note.created_at)

    async def delete(self, note_id: str) -> None:
        self._by_id.pop(note_id, None)

    async def delete_for_deck(self, deck_id: str) -> None:
        for note_id in [n.id for n in self._by_id.values() if n.deck_id == deck_id]:
            del self._by_id[note_id]


class InMemoryCardRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, Card] = {}

    async def add(self, card: Card) -> None:
        self._by_id[card.id] = card

    async def get(self, card_id: str) -> Card | None:
        return self._by_id.get(card_id)

    async def list_for_deck(self, deck_id: str) -> list[Card]:
        cards = [c for c in self._by_id.values() if c.deck_id == deck_id]
        return sorted(cards, key=lambda c: (c.created_at, c.ordinal))

    async def update(self, card: Card) -> None:
        self._by_id[card.id] = card

    async def delete_for_note(self, note_id: str) -> None:
        for card_id in [c.id for c in self._by_id.values() if c.note_id == note_id]:
            del self._by_id[card_id]

    async def delete_for_deck(self, deck_id: str) -> None:
        for card_id in [c.id for c in self._by_id.values() if c.deck_id == deck_id]:
            del self._by_id[card_id]


class InMemoryScheduleRepository:
    def __init__(self) -> None:
        self._by_card: dict[str, CardSchedule] = {}

    async def get(self, card_id: str) -> CardSchedule | None:
        return self._by_card.get(card_id)

    async def upsert(self, schedule: CardSchedule) -> None:
        self._by_card[schedule.card_id] = schedule

    async def delete(self, card_id: str) -> None:
        self._by_card.pop(card_id, None)


class InMemoryReviewLogRepository:
    def __init__(self) -> None:
        self._logs: list[ReviewLog] = []

    async def append(self, log: ReviewLog) -> None:
        if log.idempotency_key is not None and any(
            existing.owner_id == log.owner_id
            and existing.card_id == log.card_id
            and existing.idempotency_key == log.idempotency_key
            for existing in self._logs
        ):
            raise DuplicateReview(log.idempotency_key)
        self._logs.append(log)

    async def list_for_owner(self, owner_id: str) -> list[ReviewLog]:
        owned = [log for log in self._logs if log.owner_id == owner_id]
        return sorted(owned, key=lambda log: log.reviewed_at)

    async def delete_for_card(self, card_id: str) -> None:
        self._logs = [log for log in self._logs if log.card_id != card_id]

    async def delete_for_owner(self, owner_id: str) -> None:
        self._logs = [log for log in self._logs if log.owner_id != owner_id]


class InMemoryObjectStore:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self._blobs[key] = data

    async def get(self, key: str) -> bytes | None:
        return self._blobs.get(key)

    async def delete(self, key: str) -> None:
        self._blobs.pop(key, None)


class InMemoryAiJobRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, AiJob] = {}

    async def add(self, job: AiJob) -> None:
        self._by_id[job.id] = job

    async def get(self, job_id: str) -> AiJob | None:
        return self._by_id.get(job_id)

    async def update(self, job: AiJob) -> None:
        self._by_id[job.id] = job

    async def try_claim(self, job_id: str, now: datetime) -> AiJob | None:
        job = self._by_id.get(job_id)
        if job is None or job.status is not JobStatus.QUEUED:
            return None
        claimed = job.started(now)
        self._by_id[job_id] = claimed
        return claimed

    async def request_cancel(self, job_id: str, now: datetime) -> bool:
        job = self._by_id.get(job_id)
        if job is None or job.is_terminal or job.cancel_requested:
            return False
        self._by_id[job_id] = job.with_cancel_requested(now)
        return True

    async def list_running(self) -> list[AiJob]:
        return [j for j in self._by_id.values() if j.status is JobStatus.RUNNING]

    async def list_for_owner(self, owner_id: str) -> list[AiJob]:
        return [j for j in self._by_id.values() if j.owner_id == owner_id]

    async def delete(self, job_id: str) -> None:
        self._by_id.pop(job_id, None)


class InMemoryDraftCardRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, DraftCardRecord] = {}

    async def add(self, record: DraftCardRecord) -> None:
        self._by_id[record.id] = record

    async def get(self, record_id: str) -> DraftCardRecord | None:
        return self._by_id.get(record_id)

    async def list_for_job(self, job_id: str) -> list[DraftCardRecord]:
        records = [r for r in self._by_id.values() if r.job_id == job_id]
        return sorted(records, key=lambda r: (r.created_at, r.source_page))

    async def delete(self, record_id: str) -> None:
        self._by_id.pop(record_id, None)

    async def delete_for_job(self, job_id: str) -> None:
        for record_id in [r.id for r in self._by_id.values() if r.job_id == job_id]:
            del self._by_id[record_id]


class InMemoryJobQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)


class InMemoryQuotaRepository:
    def __init__(self, limit: int = DEFAULT_MONTHLY_PAGES) -> None:
        self._limit = limit
        self._used: dict[tuple[str, str], int] = {}

    async def try_reserve_pages(self, owner_id: str, pages: int, period: str) -> bool:
        key = (owner_id, period)
        used = self._used.get(key, 0)
        if used + pages > self._limit:
            return False
        self._used[key] = used + pages
        return True

    async def delete_for_owner(self, owner_id: str) -> None:
        for key in [k for k in self._used if k[0] == owner_id]:
            del self._used[key]


class InMemoryGoalRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, Goal] = {}

    async def add(self, goal: Goal) -> None:
        self._by_id[goal.id] = goal

    async def get(self, goal_id: str) -> Goal | None:
        return self._by_id.get(goal_id)

    async def list_for_owner(self, owner_id: str) -> list[Goal]:
        owned = [g for g in self._by_id.values() if g.owner_id == owner_id]
        return sorted(owned, key=lambda g: g.created_at, reverse=True)

    async def update(self, goal: Goal) -> None:
        self._by_id[goal.id] = goal

    async def delete(self, goal_id: str) -> None:
        self._by_id.pop(goal_id, None)


class InMemoryMediaRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, MediaAsset] = {}

    async def add(self, asset: MediaAsset) -> None:
        self._by_id[asset.id] = asset

    async def get(self, media_id: str) -> MediaAsset | None:
        return self._by_id.get(media_id)

    async def list_for_owner(self, owner_id: str) -> list[MediaAsset]:
        owned = [a for a in self._by_id.values() if a.owner_id == owner_id]
        return sorted(owned, key=lambda a: a.created_at)

    async def delete_for_owner(self, owner_id: str) -> None:
        for media_id in [a.id for a in self._by_id.values() if a.owner_id == owner_id]:
            del self._by_id[media_id]


class InMemoryMockExamRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, MockExam] = {}

    async def add(self, exam: MockExam) -> None:
        self._by_id[exam.id] = exam

    async def get(self, exam_id: str) -> MockExam | None:
        return self._by_id.get(exam_id)

    async def delete_for_deck(self, deck_id: str) -> None:
        for exam_id in [e.id for e in self._by_id.values() if e.deck_id == deck_id]:
            del self._by_id[exam_id]

    async def delete_for_owner(self, owner_id: str) -> None:
        for exam_id in [e.id for e in self._by_id.values() if e.owner_id == owner_id]:
            del self._by_id[exam_id]


class InMemoryPassageRepository:
    def __init__(self) -> None:
        self._rows: list[Passage] = []

    async def add_many(self, passages: list[Passage]) -> None:
        # Same rule as the SQL unique index: one row per (deck, file, page, ordinal).
        seen = {(p.deck_id, p.file_id, p.page, p.ordinal) for p in self._rows}
        for passage in passages:
            key = (passage.deck_id, passage.file_id, passage.page, passage.ordinal)
            if key not in seen:
                seen.add(key)
                self._rows.append(passage)

    async def list_for_deck(self, deck_id: str) -> list[Passage]:
        owned = [p for p in self._rows if p.deck_id == deck_id]
        return sorted(owned, key=lambda p: (p.file_id, p.page, p.ordinal))

    async def indexed_files(self, deck_id: str) -> set[str]:
        return {p.file_id for p in self._rows if p.deck_id == deck_id}

    async def delete_for_deck(self, deck_id: str) -> None:
        self._rows = [p for p in self._rows if p.deck_id != deck_id]

    async def delete_for_owner(self, owner_id: str) -> None:
        self._rows = [p for p in self._rows if p.owner_id != owner_id]
