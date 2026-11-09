# SPDX-License-Identifier: AGPL-3.0-only
"""In-memory adapters for tests and local experiments. Not for production.

The methods are async to satisfy the repository ports. Each runs to completion
without awaiting, so on a single event loop it is effectively atomic, which is
what the invite single-use guarantee relies on here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

from flashcards.domain.ai import DraftCardRecord
from flashcards.domain.audit import AuditEvent
from flashcards.domain.chat import Passage
from flashcards.domain.decks import Deck
from flashcards.domain.goals import Goal
from flashcards.domain.invites import Invite
from flashcards.domain.jobs import AiJob, JobStatus
from flashcards.domain.login_throttle import ThrottleState, after_attempt
from flashcards.domain.media import MediaAsset
from flashcards.domain.mock_exam import MockExam
from flashcards.domain.note_types import CustomNoteType
from flashcards.domain.notes import Card, Note
from flashcards.domain.planner import PlanBaseline
from flashcards.domain.progress import (
    ActivityBucket,
    DueBucket,
    StudySettings,
    bucket_start,
)
from flashcards.domain.quotas import DEFAULT_MONTHLY_PAGES
from flashcards.domain.scheduling import (
    CardSchedule,
    DuplicateReview,
    LastAnswer,
    Rating,
    ReviewLog,
)
from flashcards.domain.sessions import Session
from flashcards.domain.sharing import ShareLink
from flashcards.domain.user_tokens import TokenPurpose, UserToken
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

    async def revoke_created_by(self, user_id: str, now: datetime) -> list[str]:
        ids = sorted(i.id for i in self._by_id.values() if i.created_by == user_id)
        return [invite_id for invite_id in ids if await self.try_revoke(invite_id, now)]


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

    async def get_for_update(self, user_id: str) -> User | None:
        return self._by_id.get(user_id)  # one event loop: nothing runs in between

    async def lock_active_admins(self) -> list[User]:
        return sorted((u for u in self._by_id.values() if u.is_active_admin), key=lambda u: u.id)

    async def list_all(self) -> list[User]:
        return sorted(self._by_id.values(), key=lambda u: (u.created_at, u.id))

    async def update(self, user: User) -> None:
        if user.id in self._by_id:
            self._by_id[user.id] = user

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

    async def revoke_all_for_user(self, user_id: str, now: datetime) -> None:
        for session in list(self._by_id.values()):
            if session.user_id == user_id and session.revoked_at is None:
                self._by_id[session.id] = session.with_revoked(now)

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

    async def list_for_deck(self, deck_id: str, limit: int | None = None) -> list[Note]:
        notes = [note for note in self._by_id.values() if note.deck_id == deck_id]
        ordered = sorted(notes, key=lambda note: note.created_at)
        return ordered if limit is None else ordered[:limit]

    async def list_for_owner(self, owner_id: str) -> list[Note]:
        notes = [note for note in self._by_id.values() if note.owner_id == owner_id]
        return sorted(notes, key=lambda note: (note.created_at, note.id))

    async def lock_many(self, note_ids: Sequence[str]) -> list[Note]:
        return [self._by_id[nid] for nid in sorted(set(note_ids)) if nid in self._by_id]

    async def update(self, note: Note) -> None:
        if note.id in self._by_id:
            self._by_id[note.id] = note

    async def count_for_deck(self, deck_id: str) -> int:
        return sum(1 for note in self._by_id.values() if note.deck_id == deck_id)

    async def list_for_note_type(self, note_type_id: str) -> list[Note]:
        notes = [n for n in self._by_id.values() if n.note_type_id == note_type_id]
        return sorted(notes, key=lambda note: (note.created_at, note.id))

    async def count_for_note_type(self, note_type_id: str) -> int:
        return sum(1 for note in self._by_id.values() if note.note_type_id == note_type_id)

    async def delete(self, note_id: str) -> None:
        self._by_id.pop(note_id, None)

    async def delete_for_deck(self, deck_id: str) -> None:
        for note_id in [n.id for n in self._by_id.values() if n.deck_id == deck_id]:
            del self._by_id[note_id]


class InMemoryNoteTypeRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, CustomNoteType] = {}

    async def add(self, note_type: CustomNoteType) -> None:
        self._by_id[note_type.id] = note_type

    async def get(self, note_type_id: str) -> CustomNoteType | None:
        return self._by_id.get(note_type_id)

    async def lock(self, note_type_id: str) -> CustomNoteType | None:
        return self._by_id.get(note_type_id)  # one event loop: nothing to lock against

    async def list_for_owner(self, owner_id: str) -> list[CustomNoteType]:
        owned = [t for t in self._by_id.values() if t.owner_id == owner_id]
        return sorted(owned, key=lambda t: (t.created_at, t.id))

    async def count_for_owner(self, owner_id: str) -> int:
        return sum(1 for t in self._by_id.values() if t.owner_id == owner_id)

    async def update(self, note_type: CustomNoteType) -> None:
        if note_type.id in self._by_id:
            self._by_id[note_type.id] = note_type

    async def delete(self, note_type_id: str) -> None:
        self._by_id.pop(note_type_id, None)

    async def delete_for_owner(self, owner_id: str) -> None:
        for note_type_id in [t.id for t in self._by_id.values() if t.owner_id == owner_id]:
            del self._by_id[note_type_id]


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

    async def list_for_owner(self, owner_id: str) -> list[Card]:
        cards = [c for c in self._by_id.values() if c.owner_id == owner_id]
        return sorted(cards, key=lambda c: (c.created_at, c.ordinal, c.id))

    async def update(self, card: Card) -> None:
        self._by_id[card.id] = card

    async def get_many(self, card_ids: Sequence[str]) -> list[Card]:
        return [self._by_id[cid] for cid in dict.fromkeys(card_ids) if cid in self._by_id]

    async def list_for_notes(self, note_ids: Sequence[str]) -> list[Card]:
        wanted = set(note_ids)
        cards = [c for c in self._by_id.values() if c.note_id in wanted]
        return sorted(cards, key=lambda c: (c.note_id, c.ordinal))

    async def delete(self, card_id: str) -> None:
        self._by_id.pop(card_id, None)

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

    async def get_many(self, card_ids: Sequence[str]) -> dict[str, CardSchedule]:
        return {cid: self._by_card[cid] for cid in card_ids if cid in self._by_card}

    async def upsert(self, schedule: CardSchedule) -> None:
        self._by_card[schedule.card_id] = schedule

    async def delete(self, card_id: str) -> None:
        self._by_card.pop(card_id, None)


class InMemoryReviewLogRepository:
    def __init__(self) -> None:
        self._logs: list[ReviewLog] = []
        self._last: dict[str, LastAnswer] = {}

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

    async def lock_owner(self, owner_id: str) -> None:
        return None  # one event loop and no awaits inside a method: nothing interleaves

    async def get(self, log_id: str) -> ReviewLog | None:
        return next((log for log in self._logs if log.id == log_id), None)

    async def find_by_key(
        self, owner_id: str, card_id: str, idempotency_key: str
    ) -> ReviewLog | None:
        return next(
            (
                log
                for log in self._logs
                if log.owner_id == owner_id
                and log.card_id == card_id
                and log.idempotency_key == idempotency_key
            ),
            None,
        )

    async def set_last_answer(self, log: ReviewLog) -> None:
        self._last[log.owner_id] = LastAnswer(
            log.owner_id, log.id, log.card_id, log.idempotency_key
        )

    async def last_answer(self, owner_id: str) -> LastAnswer | None:
        return self._last.get(owner_id)

    async def mark_undone(self, owner_id: str) -> None:
        last = self._last.get(owner_id)
        if last is not None:
            self._last[owner_id] = replace(last, undone=True)

    async def try_delete(self, log_id: str) -> bool:
        kept = [log for log in self._logs if log.id != log_id]
        removed = len(kept) != len(self._logs)
        self._logs = kept
        return removed

    async def delete_for_card(self, card_id: str) -> None:
        self._logs = [log for log in self._logs if log.card_id != card_id]

    async def delete_for_owner(self, owner_id: str) -> None:
        self._logs = [log for log in self._logs if log.owner_id != owner_id]
        self._last.pop(owner_id, None)


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

    async def try_retry(self, job_id: str, now: datetime) -> AiJob | None:
        job = self._by_id.get(job_id)
        if job is None or not job.can_retry:
            return None
        queued = job.retried(now)
        self._by_id[job_id] = queued
        return queued

    async def request_cancel(self, job_id: str, now: datetime) -> bool:
        job = self._by_id.get(job_id)
        if job is None or job.is_terminal or job.cancel_requested:
            return False
        self._by_id[job_id] = job.with_cancel_requested(now)
        return True

    async def list_running(self) -> list[AiJob]:
        return [j for j in self._by_id.values() if j.status is JobStatus.RUNNING]

    async def list_queued(self) -> list[AiJob]:
        return [j for j in self._by_id.values() if j.status is JobStatus.QUEUED]

    async def try_requeue_interrupted(
        self, job_id: str, attempt: int, now: datetime
    ) -> AiJob | None:
        job = self._by_id.get(job_id)
        if job is None or job.status is not JobStatus.RUNNING or job.attempt != attempt:
            return None
        recovered = job.requeued_after_interruption(now)
        self._by_id[job_id] = recovered
        return recovered

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

    async def list_for_owner(self, owner_id: str) -> list[MockExam]:
        owned = [e for e in self._by_id.values() if e.owner_id == owner_id]
        return sorted(owned, key=lambda e: e.created_at)

    async def try_mark_submitted(self, exam_id: str, now: datetime) -> bool:
        exam = self._by_id.get(exam_id)
        if exam is None or exam.submitted_at is not None:
            return False
        self._by_id[exam_id] = replace(exam, submitted_at=now)
        return True

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


class InMemoryShareLinkRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, ShareLink] = {}

    async def add(self, link: ShareLink) -> None:
        self._by_id[link.id] = link

    async def get(self, link_id: str) -> ShareLink | None:
        return self._by_id.get(link_id)

    async def get_by_token_hash(self, token_hash: str) -> ShareLink | None:
        return next((x for x in self._by_id.values() if x.token_hash == token_hash), None)

    async def list_for_deck(self, deck_id: str) -> list[ShareLink]:
        links = [x for x in self._by_id.values() if x.deck_id == deck_id]
        return sorted(links, key=lambda x: x.created_at)

    async def list_for_owner(self, owner_id: str) -> list[ShareLink]:
        links = [x for x in self._by_id.values() if x.owner_id == owner_id]
        return sorted(links, key=lambda x: x.created_at)

    async def update(self, link: ShareLink) -> None:
        self._by_id[link.id] = link

    async def delete_for_deck(self, deck_id: str) -> None:
        for link_id in [x.id for x in self._by_id.values() if x.deck_id == deck_id]:
            del self._by_id[link_id]

    async def delete_for_owner(self, owner_id: str) -> None:
        for link_id in [x.id for x in self._by_id.values() if x.owner_id == owner_id]:
            del self._by_id[link_id]


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        self._events.append(event)

    async def list_recent(self, *, limit: int, before: str | None = None) -> list[AuditEvent]:
        events = self._events  # in recording order
        if before is not None:
            ids = [e.id for e in events]
            events = events[: ids.index(before)] if before in ids else []
        return events[::-1][:limit]


class InMemoryLoginThrottleRepository:
    def __init__(self) -> None:
        self._by_key: dict[str, ThrottleState] = {}

    async def get(self, key: str) -> ThrottleState | None:
        return self._by_key.get(key)

    async def register_attempt(self, key: str, now: datetime) -> ThrottleState:
        # No await between the read and the write, so this is atomic on the loop.
        state = after_attempt(self._by_key.get(key), key, now)
        self._by_key[key] = state
        return state

    async def delete(self, key: str) -> None:
        self._by_key.pop(key, None)


class InMemoryUserTokenRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, UserToken] = {}

    async def add(self, token: UserToken) -> None:
        self._by_id[token.id] = token

    async def get_by_token_hash(self, token_hash: str) -> UserToken | None:
        return next((t for t in self._by_id.values() if t.token_hash == token_hash), None)

    async def latest_for_user(self, user_id: str, purpose: TokenPurpose) -> UserToken | None:
        mine = [t for t in self._by_id.values() if t.user_id == user_id and t.purpose is purpose]
        return max(mine, key=lambda t: t.created_at, default=None)

    async def try_use(self, token_id: str, now: datetime) -> bool:
        token = self._by_id.get(token_id)
        if token is None or token.used_at is not None:
            return False
        self._by_id[token_id] = replace(token, used_at=now)
        return True

    async def invalidate_for_user(
        self, user_id: str, purpose: TokenPurpose, now: datetime
    ) -> None:
        for token in list(self._by_id.values()):
            if token.user_id == user_id and token.purpose is purpose and token.used_at is None:
                self._by_id[token.id] = replace(token, used_at=now)

    async def delete_for_user(self, user_id: str) -> None:
        for token_id in [t.id for t in self._by_id.values() if t.user_id == user_id]:
            del self._by_id[token_id]


class InMemoryStudySettingsRepository:
    def __init__(self) -> None:
        self._by_owner: dict[str, StudySettings] = {}

    async def get(self, owner_id: str) -> StudySettings | None:
        return self._by_owner.get(owner_id)

    async def save(self, settings: StudySettings) -> None:
        self._by_owner[settings.owner_id] = settings

    async def delete_for_owner(self, owner_id: str) -> None:
        self._by_owner.pop(owner_id, None)


class InMemoryPlanBaselineRepository:
    """Baselines go with their goal, as the SQL foreign key cascade does."""

    def __init__(self, goals: InMemoryGoalRepository) -> None:
        self._goals = goals
        self._by_goal: dict[str, PlanBaseline] = {}

    async def get(self, goal_id: str) -> PlanBaseline | None:
        await self._drop_if_goal_gone(goal_id)
        return self._by_goal.get(goal_id)

    async def add_if_absent(self, baseline: PlanBaseline) -> PlanBaseline:
        await self._drop_if_goal_gone(baseline.goal_id)
        return self._by_goal.setdefault(baseline.goal_id, baseline)

    async def _drop_if_goal_gone(self, goal_id: str) -> None:
        if await self._goals.get(goal_id) is None:
            self._by_goal.pop(goal_id, None)

    async def save(self, baseline: PlanBaseline) -> None:
        self._by_goal[baseline.goal_id] = baseline


class InMemoryStudyStatsRepository:
    """Aggregates over the in-memory repositories, bucketed like the SQL adapter."""

    def __init__(
        self,
        cards: InMemoryCardRepository,
        schedules: InMemoryScheduleRepository,
        logs: InMemoryReviewLogRepository,
    ) -> None:
        self._cards = cards
        self._schedules = schedules
        self._logs = logs

    async def review_activity(
        self,
        owner_id: str,
        *,
        since: datetime,
        until: datetime,
        deck_ids: Sequence[str] | None = None,
    ) -> list[ActivityBucket]:
        in_decks = await self._card_ids(owner_id, deck_ids)
        totals: dict[datetime, tuple[int, int]] = {}
        for log in await self._logs.list_for_owner(owner_id):
            if not since <= log.reviewed_at < until:
                continue
            if in_decks is not None and log.card_id not in in_decks:
                continue
            start = bucket_start(log.reviewed_at)
            reviews, recalled = totals.get(start, (0, 0))
            totals[start] = (reviews + 1, recalled + (log.rating >= Rating.HARD))
        return [ActivityBucket(start, r, k) for start, (r, k) in sorted(totals.items())]

    async def due_forecast(
        self,
        owner_id: str,
        *,
        floor: datetime,
        until: datetime,
        deck_ids: Sequence[str] | None = None,
    ) -> list[DueBucket]:
        wanted = None if deck_ids is None else set(deck_ids)
        cards = [
            c
            for c in await self._cards.list_for_owner(owner_id)
            if not c.is_suspended and (wanted is None or c.deck_id in wanted)
        ]
        schedules = await self._schedules.get_many([c.id for c in cards])
        totals: dict[datetime, int] = {}
        for schedule in schedules.values():
            if schedule.due_at >= until:
                continue
            start = bucket_start(max(schedule.due_at, floor))
            totals[start] = totals.get(start, 0) + 1
        return [DueBucket(start, n) for start, n in sorted(totals.items())]

    async def _card_ids(self, owner_id: str, deck_ids: Sequence[str] | None) -> set[str] | None:
        if deck_ids is None:
            return None
        wanted = set(deck_ids)
        return {c.id for c in await self._cards.list_for_owner(owner_id) if c.deck_id in wanted}


class NullUnitOfWork:
    """In memory every write is visible at once, so there is nothing to commit."""

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None
