# SPDX-License-Identifier: AGPL-3.0-only
"""Ports: interfaces the domain and application layers depend on."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol

from flashcards.domain.ai import DraftCardRecord, StructuredRequest, StructuredResult
from flashcards.domain.anki import AnkiPackage
from flashcards.domain.chat import Passage
from flashcards.domain.decks import Deck
from flashcards.domain.goals import Goal
from flashcards.domain.invites import Invite
from flashcards.domain.jobs import AiJob
from flashcards.domain.media import MediaAsset
from flashcards.domain.mock_exam import MockExam
from flashcards.domain.notes import Card, Note
from flashcards.domain.scheduling import CardSchedule, Rating, ReviewLog
from flashcards.domain.sessions import Session
from flashcards.domain.sources import ExtractedPage
from flashcards.domain.transcripts import Transcript
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

    async def list_for_deck(self, deck_id: str) -> list[Note]:
        """Return the deck's notes, oldest first."""
        ...

    async def delete(self, note_id: str) -> None: ...

    async def delete_for_deck(self, deck_id: str) -> None:
        """Delete every note in a deck."""
        ...


class CardRepository(Protocol):
    async def add(self, card: Card) -> None: ...

    async def get(self, card_id: str) -> Card | None: ...

    async def list_for_deck(self, deck_id: str) -> list[Card]:
        """Return the deck's cards, oldest first."""
        ...

    async def update(self, card: Card) -> None:
        """Persist a card change (suspend/unsuspend)."""
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

    async def delete_for_card(self, card_id: str) -> None:
        """Delete every review log for a card."""
        ...

    async def delete_for_owner(self, owner_id: str) -> None:
        """Delete every review log owned by the user (account deletion, F-IMP-5)."""
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

    async def request_cancel(self, job_id: str, now: datetime) -> bool:
        """Flag a non-terminal job for cancellation; return whether it changed."""
        ...

    async def list_running(self) -> list[AiJob]:
        """Jobs left RUNNING (e.g. by a crash), for boot-time requeue."""
        ...

    async def list_for_owner(self, owner_id: str) -> list[AiJob]:
        """The owner's jobs (for account deletion, F-IMP-5)."""
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
