# SPDX-License-Identifier: AGPL-3.0-only
"""Ports: interfaces the domain and application layers depend on."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Protocol

from flashcards.domain.ai import DraftCardRecord, StructuredRequest, StructuredResult
from flashcards.domain.anki import AnkiPackage
from flashcards.domain.audit import AuditEvent
from flashcards.domain.chat import Passage
from flashcards.domain.decks import Deck
from flashcards.domain.goals import Goal
from flashcards.domain.invites import Invite
from flashcards.domain.jobs import AiJob
from flashcards.domain.login_throttle import ThrottleState
from flashcards.domain.mail import OutgoingEmail
from flashcards.domain.media import MediaAsset
from flashcards.domain.mock_exam import MockExam
from flashcards.domain.note_types import CustomNoteType
from flashcards.domain.notes import Card, Note
from flashcards.domain.planner import PlanBaseline
from flashcards.domain.progress import ActivityBucket, DueBucket, StudySettings
from flashcards.domain.scheduling import CardSchedule, LastAnswer, Rating, ReviewLog
from flashcards.domain.sessions import Session
from flashcards.domain.sharing import ShareLink
from flashcards.domain.sources import ExtractedPage
from flashcards.domain.transcripts import Transcript
from flashcards.domain.user_tokens import TokenPurpose, UserToken
from flashcards.domain.users import User


class LLMProvider(Protocol):
    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        """Return JSON matching request.schema.

        Implementations MUST pass request.documents as model data content (never
        concatenated into request.instructions), declare no tools, constrain the
        output to request.schema, and validate the parsed JSON (raising
        flashcards.domain.ai.LlmError on transport or validation failure).
        """
        ...


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str:
        """Return a self-describing password hash (algorithm and parameters embedded)."""
        ...

    def verify(self, password_hash: str, password: str) -> bool:
        """Return whether the password matches the hash. Never raises on a mismatch."""
        ...


class UserRepository(Protocol):
    async def add(self, user: User) -> None:
        """Persist a new user.

        Raise flashcards.domain.users.EmailAlreadyRegistered if the email is
        already taken. A SQL adapter enforces this with a unique constraint and
        creates the user in the same transaction that consumes the invite.
        """
        ...

    async def get(self, user_id: str) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None: ...

    async def get_for_update(self, user_id: str) -> User | None:
        """Read the user and lock the row until the transaction ends.

        Use it before changing a user, so concurrent changes apply one after the
        other instead of overwriting each other (SELECT ... FOR UPDATE).
        """
        ...

    async def lock_active_admins(self) -> list[User]:
        """Every active admin, with their rows locked until the transaction ends.

        Taken before any change that could remove an admin, so two such changes
        cannot both see another admin remaining. Rows are locked in id order.
        """
        ...

    async def list_all(self) -> list[User]:
        """Every user, oldest first (admin user management)."""
        ...

    async def update(self, user: User) -> None:
        """Replace the stored user (the email is never changed this way)."""
        ...

    async def delete(self, user_id: str) -> None:
        """Delete the user row (account deletion, F-IMP-5)."""
        ...


class SessionRepository(Protocol):
    async def add(self, session: Session) -> None: ...

    async def get_by_token_hash(self, token_hash: str) -> Session | None: ...

    async def list_for_user(self, user_id: str) -> list[Session]:
        """Return the user's sessions, newest first, for a device list."""
        ...

    async def revoke(self, session_id: str, now: datetime) -> bool:
        """Mark an active session revoked; return whether it changed."""
        ...

    async def revoke_all_for_user(self, user_id: str, now: datetime) -> None:
        """Revoke every active session for a user (password reset, suspension)."""
        ...

    async def delete_for_user(self, user_id: str) -> None:
        """Delete every session for a user (account deletion, F-IMP-5)."""
        ...


class InviteRepository(Protocol):
    async def add(self, invite: Invite) -> None: ...

    async def get(self, invite_id: str) -> Invite | None: ...

    async def get_by_token_hash(self, token_hash: str) -> Invite | None: ...

    async def list_all(self) -> list[Invite]:
        """Return all invites, newest first, for the admin console."""
        ...

    async def try_redeem(self, invite_id: str, now: datetime) -> bool:
        """Atomically mark the invite redeemed.

        Return True only for the single caller that performed the transition, so two
        concurrent signups can never both consume the same invite. A SQL adapter
        implements this as one conditional UPDATE ... WHERE redeemed_at IS NULL.
        """
        ...

    async def try_revoke(self, invite_id: str, now: datetime) -> bool:
        """Mark an unredeemed, unrevoked invite revoked; return whether it changed."""
        ...

    async def revoke_created_by(self, user_id: str, now: datetime) -> list[str]:
        """Revoke every unredeemed, unrevoked invite the user created; return their ids."""
        ...


class DeckRepository(Protocol):
    async def add(self, deck: Deck) -> None: ...

    async def get(self, deck_id: str) -> Deck | None: ...

    async def list_for_owner(self, owner_id: str) -> list[Deck]:
        """Return the owner's decks, oldest first."""
        ...

    async def update(self, deck: Deck) -> None:
        """Persist a rename or move of an existing deck."""
        ...

    async def delete(self, deck_id: str) -> None: ...


class NoteRepository(Protocol):
    async def add(self, note: Note) -> None: ...

    async def get(self, note_id: str) -> Note | None: ...

    async def list_for_deck(self, deck_id: str, limit: int | None = None) -> list[Note]:
        """Return the deck's notes, oldest first; at most `limit` when given."""
        ...

    async def list_for_owner(self, owner_id: str) -> list[Note]:
        """Every note the owner has, oldest first (the card browser)."""
        ...

    async def lock_many(self, note_ids: Sequence[str]) -> list[Note]:
        """These notes (those that exist), locked until the transaction ends.

        Taken before changing notes, so concurrent edits, moves and tag changes
        of one note apply in turn (SELECT ... FOR UPDATE, in id order).
        """
        ...

    async def update(self, note: Note) -> None:
        """Persist a change to a note's fields, tags or deck."""
        ...

    async def count_for_deck(self, deck_id: str) -> int:
        """How many notes the deck has, without loading them."""
        ...

    async def list_for_note_type(self, note_type_id: str) -> list[Note]:
        """Every note of a custom note type, oldest first."""
        ...

    async def count_for_note_type(self, note_type_id: str) -> int:
        """How many notes use a custom note type, without loading them."""
        ...

    async def delete(self, note_id: str) -> None: ...

    async def delete_for_deck(self, deck_id: str) -> None:
        """Delete every note in a deck."""
        ...


class NoteTypeRepository(Protocol):
    async def add(self, note_type: CustomNoteType) -> None: ...

    async def get(self, note_type_id: str) -> CustomNoteType | None: ...

    async def lock(self, note_type_id: str) -> CustomNoteType | None:
        """The note type, locked until the transaction ends (SELECT ... FOR UPDATE).

        Taken before creating or editing notes of the type and before editing or
        deleting the type, so a type edit never misses a note made meanwhile.
        """
        ...

    async def list_for_owner(self, owner_id: str) -> list[CustomNoteType]:
        """The owner's note types, oldest first."""
        ...

    async def count_for_owner(self, owner_id: str) -> int: ...

    async def update(self, note_type: CustomNoteType) -> None:
        """Persist a change to a note type's name, fields or templates."""
        ...

    async def delete(self, note_type_id: str) -> None: ...

    async def delete_for_owner(self, owner_id: str) -> None:
        """Delete every note type the user owns (account deletion, after their notes)."""
        ...


class CardRepository(Protocol):
    async def add(self, card: Card) -> None: ...

    async def get(self, card_id: str) -> Card | None: ...

    async def list_for_deck(self, deck_id: str) -> list[Card]:
        """Return the deck's cards, oldest first."""
        ...

    async def list_for_owner(self, owner_id: str) -> list[Card]:
        """Every card the owner has, oldest first (the card browser)."""
        ...

    async def get_many(self, card_ids: Sequence[str]) -> list[Card]:
        """The cards with these ids that exist (any owner; callers check ownership)."""
        ...

    async def list_for_notes(self, note_ids: Sequence[str]) -> list[Card]:
        """Every card of these notes, by note then ordinal."""
        ...

    async def update(self, card: Card) -> None:
        """Persist a card change (suspend, bury, flag, deck)."""
        ...

    async def delete(self, card_id: str) -> None:
        """Delete one card (a cloze deletion removed from its note)."""
        ...

    async def delete_for_note(self, note_id: str) -> None:
        """Delete every card produced by a note."""
        ...

    async def delete_for_deck(self, deck_id: str) -> None:
        """Delete every card in a deck."""
        ...


class Scheduler(Protocol):
    def new_schedule(self, card_id: str, now: datetime) -> CardSchedule:
        """Return the initial schedule for a never-reviewed card."""
        ...

    def review(self, schedule: CardSchedule, rating: Rating, now: datetime) -> CardSchedule:
        """Return the next schedule after grading a card."""
        ...


class ScheduleRepository(Protocol):
    async def get(self, card_id: str) -> CardSchedule | None: ...

    async def get_many(self, card_ids: Sequence[str]) -> dict[str, CardSchedule]:
        """The schedules of these cards, keyed by card id; cards never reviewed are absent."""
        ...

    async def upsert(self, schedule: CardSchedule) -> None: ...

    async def delete(self, card_id: str) -> None:
        """Delete a card's schedule, if any."""
        ...


class ReviewLogRepository(Protocol):
    async def append(self, log: ReviewLog) -> None:
        """Append a review log entry.

        When the log carries an idempotency_key and one already exists for the
        same (owner_id, card_id, idempotency_key), raise
        flashcards.domain.scheduling.DuplicateReview instead of appending, so a
        retried submission is an atomic no-op, never a double count (NF-REL-1).
        """
        ...

    async def list_for_owner(self, owner_id: str) -> list[ReviewLog]:
        """Return the owner's review logs, oldest first (for data export)."""
        ...

    async def lock_owner(self, owner_id: str) -> None:
        """Serialize review writes (answer, undo) for one owner until the
        transaction ends, so an undo never interleaves with a new answer.
        A SQL adapter takes a transaction-scoped advisory lock."""
        ...

    async def get(self, log_id: str) -> ReviewLog | None: ...

    async def find_by_key(
        self, owner_id: str, card_id: str, idempotency_key: str
    ) -> ReviewLog | None:
        """The review a retried submission with this idempotency key recorded."""
        ...

    async def set_last_answer(self, log: ReviewLog) -> None:
        """Record this review as the owner's most recent answer (not undone)."""
        ...

    async def last_answer(self, owner_id: str) -> LastAnswer | None: ...

    async def mark_undone(self, owner_id: str) -> None:
        """Flag the owner's most recent answer as undone."""
        ...

    async def try_delete(self, log_id: str) -> bool:
        """Delete one review log; True only for the caller that removed it."""
        ...

    async def delete_for_card(self, card_id: str) -> None:
        """Delete every review log for a card."""
        ...

    async def delete_for_owner(self, owner_id: str) -> None:
        """Delete every review log owned by the user, and their last-answer
        record (account deletion, F-IMP-5)."""
        ...


class DocumentExtractor(Protocol):
    def sniff(self, data: bytes) -> str:
        """Return the real media type from content bytes, or raise UnsupportedMediaType.

        The declared Content-Type and filename are never trusted.
        """
        ...

    def count_pages(self, data: bytes) -> int:
        """Return the page count cheaply (images are one page), for quota reservation."""
        ...

    def extract(
        self, data: bytes, *, file_id: str, max_pages: int
    ) -> AsyncIterator[ExtractedPage]:
        """Yield one normalized page at a time (peak memory ~ one page).

        Raise flashcards.domain.sources.TooManyPages when the real page count
        exceeds max_pages, and never execute embedded content.
        """
        ...


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes | None: ...

    async def delete(self, key: str) -> None: ...


class AiJobRepository(Protocol):
    async def add(self, job: AiJob) -> None: ...

    async def get(self, job_id: str) -> AiJob | None: ...

    async def update(self, job: AiJob) -> None: ...

    async def try_claim(self, job_id: str, now: datetime) -> AiJob | None:
        """Atomically move a QUEUED job to RUNNING and return it; None if not claimable.

        Only the single caller that wins the transition gets the job back, so a
        job is never processed twice.
        """
        ...

    async def try_retry(self, job_id: str, now: datetime) -> AiJob | None:
        """Atomically re-queue a FAILED job with attempts left (AiJob.retried).

        Returns the queued job, or None when it is not retryable, so two concurrent
        retries can never both apply. A SQL adapter uses one conditional UPDATE.
        """
        ...

    async def request_cancel(self, job_id: str, now: datetime) -> bool:
        """Flag a non-terminal job for cancellation; return whether it changed."""
        ...

    async def list_running(self) -> list[AiJob]:
        """Jobs left RUNNING (e.g. by a crash), for boot-time requeue."""
        ...

    async def list_for_owner(self, owner_id: str) -> list[AiJob]:
        """The owner's jobs (for account deletion, F-IMP-5)."""
        ...

    async def list_recent(
        self, owner_id: str, *, deck_id: str | None, limit: int, offset: int
    ) -> list[AiJob]:
        """A page of the owner's jobs, newest first (created_at, then id, descending).

        With deck_id, only the jobs whose drafts target that deck.
        """
        ...

    async def delete(self, job_id: str) -> None:
        """Delete a job (its drafts cascade); used by account deletion."""
        ...


class DraftCardRepository(Protocol):
    async def add(self, record: DraftCardRecord) -> None: ...

    async def get(self, record_id: str) -> DraftCardRecord | None: ...

    async def list_for_job(self, job_id: str) -> list[DraftCardRecord]:
        """Return a job's draft cards, oldest first."""
        ...

    async def delete(self, record_id: str) -> None: ...

    async def delete_for_job(self, job_id: str) -> None: ...


class UnitOfWork(Protocol):
    """Lets long-running work make its progress visible before it finishes."""

    async def commit(self) -> None:
        """Make everything so far durable and visible to other sessions.

        Later work continues in a new transaction of the same unit.
        """
        ...

    async def rollback(self) -> None:
        """Discard everything since the last commit."""
        ...


class JobQueue(Protocol):
    async def enqueue(self, job_id: str) -> None:
        """Hand a job id to the worker to be processed (idempotent)."""
        ...


class QuotaRepository(Protocol):
    async def try_reserve_pages(self, owner_id: str, pages: int, period: str) -> bool:
        """Atomically reserve pages against the owner's limit; True if within it."""
        ...

    async def delete_for_owner(self, owner_id: str) -> None:
        """Delete the owner's quota counters (account deletion, F-IMP-5)."""
        ...


class MediaTypeSniffer(Protocol):
    def sniff(self, data: bytes) -> str:
        """Return the media content type from the bytes, or raise UnsupportedMedia.

        The declared Content-Type and filename are never trusted.
        """
        ...


class MediaRepository(Protocol):
    async def add(self, asset: MediaAsset) -> None: ...

    async def get(self, media_id: str) -> MediaAsset | None: ...

    async def list_for_owner(self, owner_id: str) -> list[MediaAsset]:
        """The owner's media assets, oldest first (for data export)."""
        ...

    async def delete_for_owner(self, owner_id: str) -> None:
        """Delete every media asset owned by the user (account deletion, F-IMP-5)."""
        ...


class PackageReader(Protocol):
    def read(self, data: bytes) -> AnkiPackage:
        """Parse an .apkg into an AnkiPackage, or raise flashcards.domain.anki errors.

        The archive is untrusted: implementations guard against path traversal and
        zip bombs and never execute embedded content (NF-SEC-2).
        """
        ...


class GoalRepository(Protocol):
    async def add(self, goal: Goal) -> None: ...

    async def get(self, goal_id: str) -> Goal | None: ...

    async def list_for_owner(self, owner_id: str) -> list[Goal]:
        """Return the owner's goals, newest first."""
        ...

    async def update(self, goal: Goal) -> None: ...

    async def delete(self, goal_id: str) -> None: ...


class MockExamRepository(Protocol):
    async def add(self, exam: MockExam) -> None: ...

    async def get(self, exam_id: str) -> MockExam | None: ...

    async def list_for_owner(self, owner_id: str) -> list[MockExam]:
        """Return the owner's mock exams, oldest first (account export)."""
        ...

    async def try_mark_submitted(self, exam_id: str, now: datetime) -> bool:
        """Record the one submission atomically; False if it was already submitted."""
        ...

    async def delete_for_deck(self, deck_id: str) -> None:
        """Delete a deck's mock exams (deck deletion)."""
        ...

    async def delete_for_owner(self, owner_id: str) -> None:
        """Delete every mock exam the owner has (account deletion)."""
        ...


class TranscriptProvider(Protocol):
    async def fetch(self, video_id: str) -> Transcript:
        """Retrieve a video's transcript, or raise TranscriptUnavailable (F-AI-2).

        Best-effort by design: transcript access is unreliable, so callers offer a
        manual paste as the fallback.
        """
        ...


class PassageRepository(Protocol):
    async def add_many(self, passages: list[Passage]) -> None: ...

    async def list_for_deck(self, deck_id: str) -> list[Passage]:
        """Return the deck's indexed passages in file, page and ordinal order."""
        ...

    async def indexed_files(self, deck_id: str) -> set[str]:
        """The source file ids already indexed for the deck."""
        ...

    async def delete_for_deck(self, deck_id: str) -> None:
        """Delete a deck's passages (deck deletion)."""
        ...

    async def delete_for_owner(self, owner_id: str) -> None:
        """Delete every passage the owner has (account deletion)."""
        ...


class ShareLinkRepository(Protocol):
    async def add(self, link: ShareLink) -> None: ...

    async def get(self, link_id: str) -> ShareLink | None: ...

    async def get_by_token_hash(self, token_hash: str) -> ShareLink | None: ...

    async def list_for_deck(self, deck_id: str) -> list[ShareLink]:
        """Return the deck's links, oldest first, revoked ones included."""
        ...

    async def list_for_owner(self, owner_id: str) -> list[ShareLink]:
        """Return every link the owner created, oldest first (account export)."""
        ...

    async def update(self, link: ShareLink) -> None: ...

    async def delete_for_deck(self, deck_id: str) -> None:
        """Delete a deck's links (deck deletion)."""
        ...

    async def delete_for_owner(self, owner_id: str) -> None:
        """Delete every link the owner created (account deletion)."""
        ...


class AuditRepository(Protocol):
    async def append(self, event: AuditEvent) -> None: ...

    async def list_recent(self, *, limit: int, before: str | None = None) -> list[AuditEvent]:
        """Newest first, in recording order.

        `before` is an event id (the last of the previous page): only events
        recorded before it are returned, so pages never skip or repeat an event.
        An unknown id gives an empty page.
        """
        ...


class LoginThrottleRepository(Protocol):
    async def get(self, key: str) -> ThrottleState | None: ...

    async def register_attempt(self, key: str, now: datetime) -> ThrottleState:
        """Count one attempt atomically (see login_throttle.after_attempt).

        Concurrent attempts must each be counted: no read-modify-write race.
        """
        ...

    async def delete(self, key: str) -> None: ...


class Mailer(Protocol):
    async def send(self, email: OutgoingEmail) -> None: ...


class UserTokenRepository(Protocol):
    async def add(self, token: UserToken) -> None: ...

    async def get_by_token_hash(self, token_hash: str) -> UserToken | None: ...

    async def latest_for_user(self, user_id: str, purpose: TokenPurpose) -> UserToken | None:
        """The most recently created token of this purpose, used or not."""
        ...

    async def try_use(self, token_id: str, now: datetime) -> bool:
        """Mark an unused token used, atomically; False if it was already used."""
        ...

    async def invalidate_for_user(
        self, user_id: str, purpose: TokenPurpose, now: datetime
    ) -> None:
        """Mark every unused token of this purpose used (a newer one replaces them)."""
        ...

    async def delete_for_user(self, user_id: str) -> None: ...


class StudySettingsRepository(Protocol):
    async def get(self, owner_id: str) -> StudySettings | None:
        """The owner's saved settings; None means the defaults apply."""
        ...

    async def save(self, settings: StudySettings) -> None:
        """Insert or replace the owner's settings."""
        ...

    async def delete_for_owner(self, owner_id: str) -> None: ...


class PlanBaselineRepository(Protocol):
    async def get(self, goal_id: str) -> PlanBaseline | None: ...

    async def add_if_absent(self, baseline: PlanBaseline) -> PlanBaseline:
        """Store the baseline unless the goal has one; return the stored one.

        Atomic, so two first reads of a plan agree on one baseline.
        """
        ...

    async def save(self, baseline: PlanBaseline) -> None:
        """Insert or replace the goal's baseline (the user accepted a new plan)."""
        ...


class StudyStatsRepository(Protocol):
    """Aggregated reads over review logs and schedules (F-PROG-1/3, F-PLAN-3).

    Results are grouped into epoch-aligned 15-minute buckets in storage, so the
    amount returned is bounded by the time range, never by the number of rows.
    deck_ids, when given, keeps only cards in those decks.
    """

    async def review_activity(
        self,
        owner_id: str,
        *,
        since: datetime,
        until: datetime,
        deck_ids: Sequence[str] | None = None,
    ) -> list[ActivityBucket]:
        """The owner's reviews with since <= reviewed_at < until, bucketed."""
        ...

    async def due_forecast(
        self,
        owner_id: str,
        *,
        floor: datetime,
        until: datetime,
        deck_ids: Sequence[str] | None = None,
    ) -> list[DueBucket]:
        """Schedules of the owner's unsuspended cards due before until, bucketed,
        with anything due before floor counted at floor (overdue cards)."""
        ...
