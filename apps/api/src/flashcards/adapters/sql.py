# SPDX-License-Identifier: AGPL-3.0-only
"""SQLAlchemy async repository adapters bound to a single AsyncSession.

One session (and transaction) is shared by all three repositories within a
request, so a use case like registration commits the invite redemption and the
user insert together, or not at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import (
    CursorResult,
    Result,
    Select,
    and_,
    case,
    delete,
    func,
    null,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from flashcards.adapters.db import (
    AiBlobRow,
    AiJobRow,
    AiQuotaRow,
    AiUsageRow,
    AuditEventRow,
    CardRow,
    CardScheduleRow,
    DeckRow,
    DraftCardRow,
    GoalRow,
    InstanceQuotaRow,
    InviteRow,
    LoginThrottleRow,
    MediaRow,
    MockExamRow,
    NoteRow,
    NoteTypeRow,
    PassageRow,
    ReviewLogRow,
    SessionActivityRow,
    SessionRow,
    ShareLinkRow,
    UserQuotaRow,
    UserRow,
    UserTokenRow,
    aijob_from_row,
    aijob_to_row,
    audit_from_row,
    audit_to_row,
    card_from_row,
    card_to_row,
    deck_from_row,
    deck_to_row,
    draft_from_row,
    draft_to_row,
    goal_from_row,
    goal_to_row,
    invite_from_row,
    invite_to_row,
    media_from_row,
    media_to_row,
    mock_exam_from_row,
    mock_exam_to_row,
    note_from_row,
    note_to_row,
    note_type_from_row,
    note_type_to_row,
    passage_from_row,
    review_log_from_row,
    review_log_to_row,
    schedule_from_row,
    schedule_to_row,
    session_from_row,
    session_to_row,
    share_link_from_row,
    share_link_to_row,
    throttle_from_row,
    usage_to_row,
    user_from_row,
    user_to_row,
    user_token_from_row,
    user_token_to_row,
)
from flashcards.domain.ai import DraftCardRecord
from flashcards.domain.audit import AuditEvent
from flashcards.domain.chat import Passage
from flashcards.domain.decks import Deck
from flashcards.domain.goals import Goal
from flashcards.domain.invites import Invite, Role
from flashcards.domain.jobs import AiJob, JobStatus
from flashcards.domain.login_throttle import (
    FAILURE_WINDOW,
    LOCKOUT,
    MAX_FAILED_LOGINS,
    ThrottleState,
    after_attempt,
)
from flashcards.domain.media import MediaAsset
from flashcards.domain.mock_exam import MockExam
from flashcards.domain.note_types import CustomNoteType
from flashcards.domain.notes import Card, Note
from flashcards.domain.quotas import DEFAULT_MONTHLY_PAGES, InstanceQuotas
from flashcards.domain.scheduling import CardSchedule, DuplicateReview, ReviewLog
from flashcards.domain.sessions import Session
from flashcards.domain.sharing import ShareLink
from flashcards.domain.usage import AiUsage, ProviderUsage, UserUsage
from flashcards.domain.user_tokens import TokenPurpose, UserToken
from flashcards.domain.users import EmailAlreadyRegistered, User

_IN_CHUNK = 5_000  # ids per IN (...) list, well under the bind-parameter cap
_TERMINAL = (JobStatus.SUCCEEDED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value)


def _changed_one(result: Result[object]) -> bool:
    """True when a DML statement affected exactly one row."""
    return cast("CursorResult[object]", result).rowcount == 1


class SqlInviteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, invite: Invite) -> None:
        self._session.add(invite_to_row(invite))
        await self._session.flush()

    async def get(self, invite_id: str) -> Invite | None:
        row = await self._session.get(InviteRow, invite_id)
        return invite_from_row(row) if row is not None else None

    async def get_by_token_hash(self, token_hash: str) -> Invite | None:
        row = await self._session.scalar(
            select(InviteRow).where(InviteRow.token_hash == token_hash)
        )
        return invite_from_row(row) if row is not None else None

    async def list_all(self) -> list[Invite]:
        rows = await self._session.scalars(
            select(InviteRow).order_by(InviteRow.created_at.desc())
        )
        return [invite_from_row(row) for row in rows]

    async def try_redeem(self, invite_id: str, now: datetime) -> bool:
        result = await self._session.execute(
            update(InviteRow)
            .where(
                InviteRow.id == invite_id,
                InviteRow.redeemed_at.is_(None),
                InviteRow.revoked_at.is_(None),
            )
            .values(redeemed_at=now)
        )
        return _changed_one(result)

    async def try_revoke(self, invite_id: str, now: datetime) -> bool:
        result = await self._session.execute(
            update(InviteRow)
            .where(
                InviteRow.id == invite_id,
                InviteRow.redeemed_at.is_(None),
                InviteRow.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return _changed_one(result)

    async def try_rotate_token(self, invite_id: str, token_hash: str, now: datetime) -> bool:
        result = await self._session.execute(
            update(InviteRow)
            .where(
                InviteRow.id == invite_id,
                InviteRow.redeemed_at.is_(None),
                InviteRow.revoked_at.is_(None),
                InviteRow.expires_at > now,
            )
            .values(token_hash=token_hash)
        )
        return _changed_one(result)

    async def revoke_created_by(self, user_id: str, now: datetime) -> list[str]:
        revoked = await self._session.scalars(
            update(InviteRow)
            .where(
                InviteRow.created_by == user_id,
                InviteRow.redeemed_at.is_(None),
                InviteRow.revoked_at.is_(None),
            )
            .values(revoked_at=now)
            .returning(InviteRow.id)
        )
        return sorted(revoked)


class SqlUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> None:
        self._session.add(user_to_row(user))
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise EmailAlreadyRegistered(user.email) from exc

    async def get(self, user_id: str) -> User | None:
        row = await self._session.get(UserRow, user_id)
        return user_from_row(row) if row is not None else None

    async def get_by_email(self, email: str) -> User | None:
        row = await self._session.scalar(select(UserRow).where(UserRow.email == email))
        return user_from_row(row) if row is not None else None

    async def get_for_update(self, user_id: str) -> User | None:
        row = await self._session.scalar(
            select(UserRow).where(UserRow.id == user_id).with_for_update()
        )
        return user_from_row(row) if row is not None else None

    async def lock_active_admins(self) -> list[User]:
        rows = await self._session.scalars(
            select(UserRow)
            .where(UserRow.role == Role.ADMIN.value, UserRow.suspended_at.is_(None))
            .order_by(UserRow.id)
            .with_for_update()
        )
        return [user_from_row(row) for row in rows]

    async def list_all(self) -> list[User]:
        rows = await self._session.scalars(
            select(UserRow).order_by(UserRow.created_at, UserRow.id)
        )
        return [user_from_row(row) for row in rows]

    async def update(self, user: User) -> None:
        if await self._session.get(UserRow, user.id) is not None:
            await self._session.merge(user_to_row(user))
            await self._session.flush()

    async def delete(self, user_id: str) -> None:
        await self._session.execute(delete(UserRow).where(UserRow.id == user_id))


class SqlSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _with_activity(self) -> Select[SessionRow, datetime]:
        return select(SessionRow, SessionActivityRow.last_seen_at).outerjoin(
            SessionActivityRow, SessionActivityRow.session_id == SessionRow.id
        )

    async def add(self, session: Session) -> None:
        self._session.add(session_to_row(session))
        await self._session.flush()
        self._session.add(
            SessionActivityRow(
                session_id=session.id, last_seen_at=session.last_seen_at or session.created_at
            )
        )
        await self._session.flush()

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        found = (
            await self._session.execute(
                self._with_activity().where(SessionRow.token_hash == token_hash)
            )
        ).first()
        return session_from_row(found[0], found[1]) if found is not None else None

    async def list_for_user(self, user_id: str) -> list[Session]:
        rows = await self._session.execute(
            self._with_activity()
            .where(SessionRow.user_id == user_id)
            .order_by(SessionRow.created_at.desc())
        )
        return [session_from_row(row, last_seen) for row, last_seen in rows.tuples()]

    async def revoke(self, session_id: str, now: datetime) -> bool:
        result = await self._session.execute(
            update(SessionRow)
            .where(SessionRow.id == session_id, SessionRow.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return _changed_one(result)

    async def revoke_all_for_user(self, user_id: str, now: datetime) -> None:
        await self._session.execute(
            update(SessionRow)
            .where(SessionRow.user_id == user_id, SessionRow.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    async def revoke_others(self, user_id: str, keep_session_id: str, now: datetime) -> int:
        revoked = await self._session.scalars(
            update(SessionRow)
            .where(
                SessionRow.user_id == user_id,
                SessionRow.id != keep_session_id,
                SessionRow.revoked_at.is_(None),
                SessionRow.expires_at > now,
            )
            .values(revoked_at=now)
            .returning(SessionRow.id)
        )
        return len(revoked.all())

    async def touch(self, session_id: str, now: datetime) -> None:
        # SKIP LOCKED: when a concurrent request of the same session is already
        # recording activity, this one skips the write rather than waiting for that
        # request's transaction to end.
        # An alias, so the subquery keeps its own FROM instead of being correlated
        # with the table the UPDATE targets.
        activity = aliased(SessionActivityRow)
        unlocked = (
            select(activity.session_id)
            .where(activity.session_id == session_id)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        await self._session.execute(
            update(SessionActivityRow)
            .where(SessionActivityRow.session_id == unlocked)
            .values(last_seen_at=now)
        )

    async def delete_for_user(self, user_id: str) -> None:
        await self._session.execute(delete(SessionRow).where(SessionRow.user_id == user_id))


class SqlDeckRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, deck: Deck) -> None:
        self._session.add(deck_to_row(deck))
        await self._session.flush()

    async def get(self, deck_id: str) -> Deck | None:
        row = await self._session.get(DeckRow, deck_id)
        return deck_from_row(row) if row is not None else None

    async def list_for_owner(self, owner_id: str) -> list[Deck]:
        rows = await self._session.scalars(
            select(DeckRow).where(DeckRow.owner_id == owner_id).order_by(DeckRow.created_at)
        )
        return [deck_from_row(row) for row in rows]

    async def update(self, deck: Deck) -> None:
        await self._session.merge(deck_to_row(deck))
        await self._session.flush()

    async def delete(self, deck_id: str) -> None:
        row = await self._session.get(DeckRow, deck_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()


class SqlNoteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, note: Note) -> None:
        self._session.add(note_to_row(note))
        await self._session.flush()

    async def get(self, note_id: str) -> Note | None:
        row = await self._session.get(NoteRow, note_id)
        return note_from_row(row) if row is not None else None

    async def list_for_deck(self, deck_id: str, limit: int | None = None) -> list[Note]:
        query = select(NoteRow).where(NoteRow.deck_id == deck_id).order_by(NoteRow.created_at)
        if limit is not None:
            query = query.limit(limit)
        rows = await self._session.scalars(query)
        return [note_from_row(row) for row in rows]

    async def list_for_owner(self, owner_id: str) -> list[Note]:
        rows = await self._session.scalars(
            select(NoteRow)
            .where(NoteRow.owner_id == owner_id)
            .order_by(NoteRow.created_at, NoteRow.id)
        )
        return [note_from_row(row) for row in rows]

    async def lock_many(self, note_ids: Sequence[str]) -> list[Note]:
        ordered = sorted(set(note_ids))
        notes: list[Note] = []
        for start in range(0, len(ordered), _IN_CHUNK):
            rows = await self._session.scalars(
                select(NoteRow)
                .where(NoteRow.id.in_(ordered[start : start + _IN_CHUNK]))
                .order_by(NoteRow.id)
                .with_for_update()
                # The rows as committed once the lock is ours, not as an earlier
                # read in this session cached them (a stale copy would be written back).
                .execution_options(populate_existing=True)
            )
            notes += [note_from_row(row) for row in rows]
        return notes

    async def update(self, note: Note) -> None:
        if await self._session.get(NoteRow, note.id) is not None:
            await self._session.merge(note_to_row(note))
            await self._session.flush()

    async def count_for_deck(self, deck_id: str) -> int:
        count = await self._session.scalar(
            select(func.count()).select_from(NoteRow).where(NoteRow.deck_id == deck_id)
        )
        return int(count or 0)

    async def list_for_note_type(self, note_type_id: str) -> list[Note]:
        rows = await self._session.scalars(
            select(NoteRow)
            .where(NoteRow.note_type_id == note_type_id)
            .order_by(NoteRow.created_at, NoteRow.id)
        )
        return [note_from_row(row) for row in rows]

    async def count_for_note_type(self, note_type_id: str) -> int:
        count = await self._session.scalar(
            select(func.count()).select_from(NoteRow).where(NoteRow.note_type_id == note_type_id)
        )
        return int(count or 0)

    async def delete(self, note_id: str) -> None:
        row = await self._session.get(NoteRow, note_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()

    async def delete_for_deck(self, deck_id: str) -> None:
        await self._session.execute(delete(NoteRow).where(NoteRow.deck_id == deck_id))


class SqlNoteTypeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, note_type: CustomNoteType) -> None:
        self._session.add(note_type_to_row(note_type))
        await self._session.flush()

    async def get(self, note_type_id: str) -> CustomNoteType | None:
        row = await self._session.get(NoteTypeRow, note_type_id)
        return note_type_from_row(row) if row is not None else None

    async def lock(self, note_type_id: str) -> CustomNoteType | None:
        row = await self._session.scalar(
            select(NoteTypeRow)
            .where(NoteTypeRow.id == note_type_id)
            .with_for_update()
            .execution_options(populate_existing=True)  # the row as committed, not cached
        )
        return note_type_from_row(row) if row is not None else None

    async def list_for_owner(self, owner_id: str) -> list[CustomNoteType]:
        rows = await self._session.scalars(
            select(NoteTypeRow)
            .where(NoteTypeRow.owner_id == owner_id)
            .order_by(NoteTypeRow.created_at, NoteTypeRow.id)
        )
        return [note_type_from_row(row) for row in rows]

    async def count_for_owner(self, owner_id: str) -> int:
        count = await self._session.scalar(
            select(func.count()).select_from(NoteTypeRow).where(NoteTypeRow.owner_id == owner_id)
        )
        return int(count or 0)

    async def update(self, note_type: CustomNoteType) -> None:
        if await self._session.get(NoteTypeRow, note_type.id) is not None:
            await self._session.merge(note_type_to_row(note_type))
            await self._session.flush()

    async def delete(self, note_type_id: str) -> None:
        await self._session.execute(delete(NoteTypeRow).where(NoteTypeRow.id == note_type_id))

    async def delete_for_owner(self, owner_id: str) -> None:
        await self._session.execute(delete(NoteTypeRow).where(NoteTypeRow.owner_id == owner_id))


class SqlCardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, card: Card) -> None:
        self._session.add(card_to_row(card))
        await self._session.flush()

    async def get(self, card_id: str) -> Card | None:
        row = await self._session.get(CardRow, card_id)
        return card_from_row(row) if row is not None else None

    async def list_for_deck(self, deck_id: str) -> list[Card]:
        rows = await self._session.scalars(
            select(CardRow)
            .where(CardRow.deck_id == deck_id)
            .order_by(CardRow.created_at, CardRow.ordinal)
        )
        return [card_from_row(row) for row in rows]

    async def list_for_owner(self, owner_id: str) -> list[Card]:
        rows = await self._session.scalars(
            select(CardRow)
            .where(CardRow.owner_id == owner_id)
            .order_by(CardRow.created_at, CardRow.ordinal, CardRow.id)
        )
        return [card_from_row(row) for row in rows]

    async def update(self, card: Card) -> None:
        await self._session.merge(card_to_row(card))
        await self._session.flush()

    async def get_many(self, card_ids: Sequence[str]) -> list[Card]:
        unique = list(dict.fromkeys(card_ids))
        found: dict[str, Card] = {}
        for start in range(0, len(unique), _IN_CHUNK):
            rows = await self._session.scalars(
                select(CardRow).where(CardRow.id.in_(unique[start : start + _IN_CHUNK]))
            )
            found.update({row.id: card_from_row(row) for row in rows})
        return [found[cid] for cid in unique if cid in found]

    async def list_for_notes(self, note_ids: Sequence[str]) -> list[Card]:
        unique = sorted(set(note_ids))
        cards: list[Card] = []
        for start in range(0, len(unique), _IN_CHUNK):
            rows = await self._session.scalars(
                select(CardRow).where(CardRow.note_id.in_(unique[start : start + _IN_CHUNK]))
            )
            cards += [card_from_row(row) for row in rows]
        return sorted(cards, key=lambda c: (c.note_id, c.ordinal))

    async def delete(self, card_id: str) -> None:
        await self._session.execute(delete(CardRow).where(CardRow.id == card_id))

    async def delete_for_note(self, note_id: str) -> None:
        await self._session.execute(delete(CardRow).where(CardRow.note_id == note_id))

    async def delete_for_deck(self, deck_id: str) -> None:
        await self._session.execute(delete(CardRow).where(CardRow.deck_id == deck_id))


class SqlScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, card_id: str) -> CardSchedule | None:
        row = await self._session.get(CardScheduleRow, card_id)
        return schedule_from_row(row) if row is not None else None

    async def get_many(self, card_ids: Sequence[str]) -> dict[str, CardSchedule]:
        found: dict[str, CardSchedule] = {}
        # Chunked: Postgres caps the number of bind parameters in one statement.
        for start in range(0, len(card_ids), _IN_CHUNK):
            chunk = card_ids[start : start + _IN_CHUNK]
            rows = await self._session.scalars(
                select(CardScheduleRow).where(CardScheduleRow.card_id.in_(chunk))
            )
            found.update({row.card_id: schedule_from_row(row) for row in rows})
        return found

    async def upsert(self, schedule: CardSchedule) -> None:
        await self._session.merge(schedule_to_row(schedule))
        await self._session.flush()

    async def delete(self, card_id: str) -> None:
        await self._session.execute(
            delete(CardScheduleRow).where(CardScheduleRow.card_id == card_id)
        )


class SqlReviewLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_owner(self, owner_id: str) -> list[ReviewLog]:
        rows = await self._session.scalars(
            select(ReviewLogRow)
            .where(ReviewLogRow.owner_id == owner_id)
            .order_by(ReviewLogRow.reviewed_at)
        )
        return [review_log_from_row(row) for row in rows]

    async def append(self, log: ReviewLog) -> None:
        if log.idempotency_key is None:
            self._session.add(review_log_to_row(log))
            await self._session.flush()
            return
        # A savepoint so a duplicate-key violation rolls back only this insert and
        # leaves the surrounding request transaction usable.
        try:
            async with self._session.begin_nested():
                self._session.add(review_log_to_row(log))
                await self._session.flush()
        except IntegrityError as exc:
            raise DuplicateReview(log.idempotency_key) from exc

    async def delete_for_card(self, card_id: str) -> None:
        await self._session.execute(
            delete(ReviewLogRow).where(ReviewLogRow.card_id == card_id)
        )

    async def delete_for_owner(self, owner_id: str) -> None:
        await self._session.execute(
            delete(ReviewLogRow).where(ReviewLogRow.owner_id == owner_id)
        )


class SqlAiJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, job: AiJob) -> None:
        self._session.add(aijob_to_row(job))
        await self._session.flush()

    async def get(self, job_id: str) -> AiJob | None:
        row = await self._session.get(AiJobRow, job_id)
        return aijob_from_row(row) if row is not None else None

    async def update(self, job: AiJob) -> None:
        await self._session.merge(aijob_to_row(job))
        await self._session.flush()

    async def try_claim(self, job_id: str, now: datetime) -> AiJob | None:
        # One conditional UPDATE: only the caller that moves QUEUED -> RUNNING gets
        # the row back (RETURNING), so a job is never processed twice.
        row = await self._session.scalar(
            update(AiJobRow)
            .where(AiJobRow.id == job_id, AiJobRow.status == JobStatus.QUEUED.value)
            .values(status=JobStatus.RUNNING.value, updated_at=now)
            .returning(AiJobRow)
        )
        return aijob_from_row(row) if row is not None else None

    async def try_retry(self, job_id: str, now: datetime) -> AiJob | None:
        # Mirrors AiJob.retried in one conditional UPDATE, so concurrent retries
        # cannot both re-queue the job or hand out an extra attempt.
        row = await self._session.scalar(
            update(AiJobRow)
            .where(
                AiJobRow.id == job_id,
                AiJobRow.status == JobStatus.FAILED.value,
                AiJobRow.attempt < AiJobRow.max_attempts,
            )
            .values(
                status=JobStatus.QUEUED.value,
                attempt=AiJobRow.attempt + 1,
                error=None,
                cancel_requested=False,
                updated_at=now,
            )
            .returning(AiJobRow)
        )
        return aijob_from_row(row) if row is not None else None

    async def request_cancel(self, job_id: str, now: datetime) -> bool:
        result = await self._session.execute(
            update(AiJobRow)
            .where(
                AiJobRow.id == job_id,
                AiJobRow.status.notin_(_TERMINAL),
                AiJobRow.cancel_requested.is_(False),
            )
            .values(cancel_requested=True, updated_at=now)
        )
        return _changed_one(result)

    async def list_running(self) -> list[AiJob]:
        rows = await self._session.scalars(
            select(AiJobRow).where(AiJobRow.status == JobStatus.RUNNING.value)
        )
        return [aijob_from_row(row) for row in rows]

    async def list_for_owner(self, owner_id: str) -> list[AiJob]:
        rows = await self._session.scalars(
            select(AiJobRow).where(AiJobRow.owner_id == owner_id)
        )
        return [aijob_from_row(row) for row in rows]

    async def delete(self, job_id: str) -> None:
        await self._session.execute(delete(AiJobRow).where(AiJobRow.id == job_id))


class SqlDraftCardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: DraftCardRecord) -> None:
        self._session.add(draft_to_row(record))
        await self._session.flush()

    async def get(self, record_id: str) -> DraftCardRecord | None:
        row = await self._session.get(DraftCardRow, record_id)
        return draft_from_row(row) if row is not None else None

    async def list_for_job(self, job_id: str) -> list[DraftCardRecord]:
        rows = await self._session.scalars(
            select(DraftCardRow)
            .where(DraftCardRow.job_id == job_id)
            .order_by(DraftCardRow.created_at, DraftCardRow.source_page)
        )
        return [draft_from_row(row) for row in rows]

    async def delete(self, record_id: str) -> None:
        row = await self._session.get(DraftCardRow, record_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()

    async def delete_for_job(self, job_id: str) -> None:
        await self._session.execute(delete(DraftCardRow).where(DraftCardRow.job_id == job_id))


class SqlObjectStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await self._session.merge(AiBlobRow(key=key, data=data, content_type=content_type))
        await self._session.flush()

    async def get(self, key: str) -> bytes | None:
        row = await self._session.get(AiBlobRow, key)
        return row.data if row is not None else None

    async def delete(self, key: str) -> None:
        row = await self._session.get(AiBlobRow, key)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()


class SqlQuotaRepository:
    def __init__(self, session: AsyncSession, limit: int = DEFAULT_MONTHLY_PAGES) -> None:
        self._session = session
        self._limit = limit

    async def try_reserve_pages(
        self, owner_id: str, pages: int, period: str, limit: int | None = None
    ) -> bool:
        limit = self._limit if limit is None else limit
        # A single upload can never exceed the whole-period limit; this also guards
        # the INSERT branch below (a fresh owner/period starts at used_pages=0).
        if pages > limit:
            return False
        # Atomic upsert: insert the first reservation, or add to an existing one only
        # while it stays within the limit. RETURNING yields a row exactly when the
        # reservation took effect, so concurrent requests can never over-reserve.
        stmt = (
            pg_insert(AiQuotaRow)
            .values(owner_id=owner_id, period=period, used_pages=pages)
            .on_conflict_do_update(
                index_elements=[AiQuotaRow.owner_id, AiQuotaRow.period],
                set_={"used_pages": AiQuotaRow.used_pages + pages},
                where=(AiQuotaRow.used_pages + pages <= limit),
            )
            .returning(AiQuotaRow.used_pages)
        )
        return await self._session.scalar(stmt) is not None

    async def used(self, owner_id: str, period: str) -> int:
        used = await self._session.scalar(
            select(AiQuotaRow.used_pages).where(
                AiQuotaRow.owner_id == owner_id, AiQuotaRow.period == period
            )
        )
        return used or 0

    async def delete_for_owner(self, owner_id: str) -> None:
        await self._session.execute(delete(AiQuotaRow).where(AiQuotaRow.owner_id == owner_id))


class SqlQuotaSettingsRepository:
    _INSTANCE_ID = 1

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_instance(self) -> InstanceQuotas | None:
        row = await self._session.get(InstanceQuotaRow, self._INSTANCE_ID)
        if row is None:
            return None
        return InstanceQuotas(
            default_monthly_pages=row.default_monthly_pages,
            monthly_spend_cap_micro_usd=row.monthly_spend_cap_micro_usd,
        )

    async def put_instance(self, quotas: InstanceQuotas) -> None:
        values = {
            "default_monthly_pages": quotas.default_monthly_pages,
            "monthly_spend_cap_micro_usd": quotas.monthly_spend_cap_micro_usd,
        }
        await self._session.execute(
            pg_insert(InstanceQuotaRow)
            .values(id=self._INSTANCE_ID, **values)
            .on_conflict_do_update(index_elements=[InstanceQuotaRow.id], set_=values)
        )

    async def get_user_pages(self, user_id: str) -> int | None:
        row = await self._session.get(UserQuotaRow, user_id)
        return row.monthly_pages if row is not None else None

    async def set_user_pages(self, user_id: str, pages: int | None) -> None:
        if pages is None:
            await self._session.execute(
                delete(UserQuotaRow).where(UserQuotaRow.user_id == user_id)
            )
            return
        await self._session.execute(
            pg_insert(UserQuotaRow)
            .values(user_id=user_id, monthly_pages=pages)
            .on_conflict_do_update(
                index_elements=[UserQuotaRow.user_id], set_={"monthly_pages": pages}
            )
        )


class SqlUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, usage: AiUsage) -> None:
        self._session.add(usage_to_row(usage))
        await self._session.flush()

    async def total_cost(self, period: str) -> int:
        total = await self._session.scalar(
            select(func.coalesce(func.sum(AiUsageRow.cost_micro_usd), 0)).where(
                AiUsageRow.period == period
            )
        )
        return int(total or 0)

    async def by_user(self, period: str) -> list[UserUsage]:
        cost = func.sum(AiUsageRow.cost_micro_usd)
        rows = await self._session.execute(
            select(
                AiUsageRow.owner_id,
                func.count(),
                func.sum(AiUsageRow.input_tokens),
                func.sum(AiUsageRow.output_tokens),
                cost,
            )
            .where(AiUsageRow.period == period)
            .group_by(AiUsageRow.owner_id)
            .order_by(cost.desc(), AiUsageRow.owner_id)
        )
        return [
            UserUsage(owner, int(calls), int(inp), int(out), int(spent))
            for owner, calls, inp, out, spent in rows.tuples()
        ]

    async def by_provider(self, period: str) -> list[ProviderUsage]:
        cost = func.sum(AiUsageRow.cost_micro_usd)
        rows = await self._session.execute(
            select(
                AiUsageRow.provider,
                AiUsageRow.model,
                func.count(),
                func.sum(AiUsageRow.input_tokens),
                func.sum(AiUsageRow.output_tokens),
                cost,
            )
            .where(AiUsageRow.period == period)
            .group_by(AiUsageRow.provider, AiUsageRow.model)
            .order_by(cost.desc(), AiUsageRow.provider, AiUsageRow.model)
        )
        return [
            ProviderUsage(provider, model, int(calls), int(inp), int(out), int(spent))
            for provider, model, calls, inp, out, spent in rows.tuples()
        ]


class SqlGoalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, goal: Goal) -> None:
        self._session.add(goal_to_row(goal))
        await self._session.flush()

    async def get(self, goal_id: str) -> Goal | None:
        row = await self._session.get(GoalRow, goal_id)
        return goal_from_row(row) if row is not None else None

    async def list_for_owner(self, owner_id: str) -> list[Goal]:
        rows = await self._session.scalars(
            select(GoalRow).where(GoalRow.owner_id == owner_id).order_by(GoalRow.created_at.desc())
        )
        return [goal_from_row(row) for row in rows]

    async def update(self, goal: Goal) -> None:
        await self._session.merge(goal_to_row(goal))
        await self._session.flush()

    async def delete(self, goal_id: str) -> None:
        row = await self._session.get(GoalRow, goal_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()


class SqlMediaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, asset: MediaAsset) -> None:
        self._session.add(media_to_row(asset))
        await self._session.flush()

    async def get(self, media_id: str) -> MediaAsset | None:
        row = await self._session.get(MediaRow, media_id)
        return media_from_row(row) if row is not None else None

    async def list_for_owner(self, owner_id: str) -> list[MediaAsset]:
        rows = await self._session.scalars(
            select(MediaRow).where(MediaRow.owner_id == owner_id).order_by(MediaRow.created_at)
        )
        return [media_from_row(row) for row in rows]

    async def delete_for_owner(self, owner_id: str) -> None:
        await self._session.execute(delete(MediaRow).where(MediaRow.owner_id == owner_id))


class SqlMockExamRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, exam: MockExam) -> None:
        self._session.add(mock_exam_to_row(exam))
        await self._session.flush()

    async def get(self, exam_id: str) -> MockExam | None:
        row = await self._session.get(MockExamRow, exam_id)
        return mock_exam_from_row(row) if row is not None else None

    async def list_for_owner(self, owner_id: str) -> list[MockExam]:
        rows = await self._session.scalars(
            select(MockExamRow)
            .where(MockExamRow.owner_id == owner_id)
            .order_by(MockExamRow.created_at)
        )
        return [mock_exam_from_row(row) for row in rows]

    async def try_mark_submitted(self, exam_id: str, now: datetime) -> bool:
        # A conditional update, so two concurrent submits cannot both succeed.
        marked = await self._session.scalar(
            update(MockExamRow)
            .where(MockExamRow.id == exam_id, MockExamRow.submitted_at.is_(None))
            .values(submitted_at=now)
            .returning(MockExamRow.id)
        )
        return marked is not None

    async def reopen(self, exam_id: str) -> None:
        await self._session.execute(
            update(MockExamRow).where(MockExamRow.id == exam_id).values(submitted_at=None)
        )

    async def delete_for_deck(self, deck_id: str) -> None:
        await self._session.execute(delete(MockExamRow).where(MockExamRow.deck_id == deck_id))

    async def delete_for_owner(self, owner_id: str) -> None:
        await self._session.execute(
            delete(MockExamRow).where(MockExamRow.owner_id == owner_id)
        )


class SqlPassageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(self, passages: list[Passage]) -> None:
        if not passages:
            return
        rows = [
            {
                "id": p.id,
                "owner_id": p.owner_id,
                "deck_id": p.deck_id,
                "file_id": p.file_id,
                "page": p.page,
                "ordinal": p.ordinal,
                "text": p.text,
            }
            for p in passages
        ]
        # A concurrent request may have indexed the same source: keep its rows.
        await self._session.execute(
            pg_insert(PassageRow)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_document_passages_position")
        )

    async def list_for_deck(self, deck_id: str) -> list[Passage]:
        rows = await self._session.scalars(
            select(PassageRow)
            .where(PassageRow.deck_id == deck_id)
            .order_by(PassageRow.file_id, PassageRow.page, PassageRow.ordinal)
        )
        return [passage_from_row(row) for row in rows]

    async def indexed_files(self, deck_id: str) -> set[str]:
        rows = await self._session.scalars(
            select(PassageRow.file_id).where(PassageRow.deck_id == deck_id).distinct()
        )
        return set(rows)

    async def delete_for_deck(self, deck_id: str) -> None:
        await self._session.execute(delete(PassageRow).where(PassageRow.deck_id == deck_id))

    async def delete_for_owner(self, owner_id: str) -> None:
        await self._session.execute(delete(PassageRow).where(PassageRow.owner_id == owner_id))


class SqlShareLinkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, link: ShareLink) -> None:
        self._session.add(share_link_to_row(link))
        await self._session.flush()

    async def get(self, link_id: str) -> ShareLink | None:
        row = await self._session.get(ShareLinkRow, link_id)
        return share_link_from_row(row) if row is not None else None

    async def get_by_token_hash(self, token_hash: str) -> ShareLink | None:
        row = await self._session.scalar(
            select(ShareLinkRow).where(ShareLinkRow.token_hash == token_hash)
        )
        return share_link_from_row(row) if row is not None else None

    async def list_for_deck(self, deck_id: str) -> list[ShareLink]:
        rows = await self._session.scalars(
            select(ShareLinkRow)
            .where(ShareLinkRow.deck_id == deck_id)
            .order_by(ShareLinkRow.created_at)
        )
        return [share_link_from_row(row) for row in rows]

    async def list_for_owner(self, owner_id: str) -> list[ShareLink]:
        rows = await self._session.scalars(
            select(ShareLinkRow)
            .where(ShareLinkRow.owner_id == owner_id)
            .order_by(ShareLinkRow.created_at)
        )
        return [share_link_from_row(row) for row in rows]

    async def update(self, link: ShareLink) -> None:
        await self._session.merge(share_link_to_row(link))
        await self._session.flush()

    async def delete_for_deck(self, deck_id: str) -> None:
        await self._session.execute(delete(ShareLinkRow).where(ShareLinkRow.deck_id == deck_id))

    async def delete_for_owner(self, owner_id: str) -> None:
        await self._session.execute(
            delete(ShareLinkRow).where(ShareLinkRow.owner_id == owner_id)
        )


class SqlAuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: AuditEvent) -> None:
        self._session.add(audit_to_row(event))
        await self._session.flush()

    async def list_recent(self, *, limit: int, before: str | None = None) -> list[AuditEvent]:
        query = select(AuditEventRow).order_by(AuditEventRow.seq.desc()).limit(limit)
        if before is not None:
            cursor = select(AuditEventRow.seq).where(AuditEventRow.id == before)
            # An unknown id makes the subquery NULL, so nothing matches: an empty page.
            query = query.where(AuditEventRow.seq < cursor.scalar_subquery())
        rows = await self._session.scalars(query)
        return [audit_from_row(row) for row in rows]


class SqlLoginThrottleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key: str) -> ThrottleState | None:
        row = await self._session.get(LoginThrottleRow, key)
        return throttle_from_row(row) if row is not None else None

    async def register_attempt(self, key: str, now: datetime) -> ThrottleState:
        # One statement, so concurrent attempts are each counted (no lost update)
        # and two first attempts cannot collide on the insert. It mirrors
        # login_throttle.after_attempt; the SET expressions read the old row.
        row = LoginThrottleRow.__table__.c
        first = after_attempt(None, key, now)
        fresh = or_(
            and_(row.locked_until.is_not(None), row.locked_until <= now),
            and_(row.locked_until.is_(None), row.window_started_at <= now - FAILURE_WINDOW),
        )
        statement = (
            pg_insert(LoginThrottleRow)
            .values(
                key=key,
                failures=first.failures,
                window_started_at=first.window_started_at,
                locked_until=first.locked_until,
            )
            .on_conflict_do_update(
                index_elements=[row.key],
                set_={
                    "failures": case((fresh, 1), else_=row.failures + 1),
                    "window_started_at": case((fresh, now), else_=row.window_started_at),
                    "locked_until": case(
                        (fresh, null()),
                        (row.locked_until.is_not(None), row.locked_until),
                        (row.failures + 1 >= MAX_FAILED_LOGINS, now + LOCKOUT),
                        else_=null(),
                    ),
                },
            )
            .returning(row.key, row.failures, row.window_started_at, row.locked_until)
        )
        saved = (await self._session.execute(statement)).one()
        return ThrottleState(
            key=saved.key,
            failures=saved.failures,
            window_started_at=saved.window_started_at,
            locked_until=saved.locked_until,
        )

    async def delete(self, key: str) -> None:
        await self._session.execute(delete(LoginThrottleRow).where(LoginThrottleRow.key == key))


class SqlUserTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: UserToken) -> None:
        self._session.add(user_token_to_row(token))
        await self._session.flush()

    async def get_by_token_hash(self, token_hash: str) -> UserToken | None:
        row = await self._session.scalar(
            select(UserTokenRow).where(UserTokenRow.token_hash == token_hash)
        )
        return user_token_from_row(row) if row is not None else None

    async def latest_for_user(self, user_id: str, purpose: TokenPurpose) -> UserToken | None:
        row = await self._session.scalar(
            select(UserTokenRow)
            .where(UserTokenRow.user_id == user_id, UserTokenRow.purpose == purpose.value)
            .order_by(UserTokenRow.created_at.desc())
            .limit(1)
        )
        return user_token_from_row(row) if row is not None else None

    async def try_use(self, token_id: str, now: datetime) -> bool:
        used = await self._session.scalar(
            update(UserTokenRow)
            .where(UserTokenRow.id == token_id, UserTokenRow.used_at.is_(None))
            .values(used_at=now)
            .returning(UserTokenRow.id)
        )
        return used is not None

    async def invalidate_for_user(
        self, user_id: str, purpose: TokenPurpose, now: datetime
    ) -> None:
        await self._session.execute(
            update(UserTokenRow)
            .where(
                UserTokenRow.user_id == user_id,
                UserTokenRow.purpose == purpose.value,
                UserTokenRow.used_at.is_(None),
            )
            .values(used_at=now)
        )

    async def delete_for_user(self, user_id: str) -> None:
        await self._session.execute(delete(UserTokenRow).where(UserTokenRow.user_id == user_id))


class SqlUnitOfWork:
    """Commits or rolls back the session's current transaction mid-unit."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
