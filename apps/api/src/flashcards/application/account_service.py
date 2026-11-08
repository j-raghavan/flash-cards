# SPDX-License-Identifier: AGPL-3.0-only
"""Account-wide data export and deletion (F-IMP-5, NF-PRIV-1).

Export gathers everything a user owns into one structure. Deletion removes all of
it: AI jobs (with their drafts and stored files), goals, decks (cascading notes,
cards, schedules and review logs through the library coordinator), the user's
sessions, and finally the account itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from flashcards.application.library_service import LibraryService
from flashcards.domain.decks import Deck
from flashcards.domain.goals import Goal
from flashcards.domain.media import MediaAsset
from flashcards.domain.notes import Card, Note
from flashcards.domain.ports import (
    AiJobRepository,
    CardRepository,
    DeckRepository,
    DraftCardRepository,
    GoalRepository,
    MediaRepository,
    MockExamRepository,
    NoteRepository,
    ObjectStore,
    QuotaRepository,
    ReviewLogRepository,
    ScheduleRepository,
    SessionRepository,
    UserRepository,
)
from flashcards.domain.scheduling import CardSchedule, ReviewLog
from flashcards.domain.users import User


@dataclass(frozen=True, slots=True)
class AccountExport:
    user: User
    decks: tuple[Deck, ...]
    notes: tuple[Note, ...]
    cards: tuple[Card, ...]
    schedules: tuple[CardSchedule, ...]
    review_logs: tuple[ReviewLog, ...]
    goals: tuple[Goal, ...]
    media: tuple[MediaAsset, ...]


class AccountService:
    def __init__(
        self,
        library: LibraryService,
        decks: DeckRepository,
        notes: NoteRepository,
        cards: CardRepository,
        schedules: ScheduleRepository,
        review_logs: ReviewLogRepository,
        goals: GoalRepository,
        ai_jobs: AiJobRepository,
        draft_cards: DraftCardRepository,
        object_store: ObjectStore,
        sessions: SessionRepository,
        users: UserRepository,
        media: MediaRepository,
        quotas: QuotaRepository,
        mock_exams: MockExamRepository,
    ) -> None:
        self._library = library
        self._decks = decks
        self._notes = notes
        self._cards = cards
        self._schedules = schedules
        self._review_logs = review_logs
        self._goals = goals
        self._ai_jobs = ai_jobs
        self._draft_cards = draft_cards
        self._object_store = object_store
        self._sessions = sessions
        self._users = users
        self._media = media
        self._quotas = quotas
        self._mock_exams = mock_exams

    async def export(self, *, user: User) -> AccountExport:
        decks = await self._decks.list_for_owner(user.id)
        notes: list[Note] = []
        cards: list[Card] = []
        schedules: list[CardSchedule] = []
        for deck in decks:
            notes.extend(await self._notes.list_for_deck(deck.id))
            deck_cards = await self._cards.list_for_deck(deck.id)
            cards.extend(deck_cards)
            for card in deck_cards:
                schedule = await self._schedules.get(card.id)
                if schedule is not None:
                    schedules.append(schedule)
        return AccountExport(
            user=user,
            decks=tuple(decks),
            notes=tuple(notes),
            cards=tuple(cards),
            schedules=tuple(schedules),
            review_logs=tuple(await self._review_logs.list_for_owner(user.id)),
            goals=tuple(await self._goals.list_for_owner(user.id)),
            media=tuple(await self._media.list_for_owner(user.id)),
        )

    async def delete(self, *, user: User) -> None:
        # In SQL mode the whole request runs in one transaction (sql_provider wraps
        # it in session.begin()), so this erasure is atomic there.
        owner_id = user.id
        # AI jobs, their drafts (cascade) and their stored source files.
        for job in await self._ai_jobs.list_for_owner(owner_id):
            await self._object_store.delete(job.source_id)
            await self._draft_cards.delete_for_job(job.id)
            await self._ai_jobs.delete(job.id)
        for goal in await self._goals.list_for_owner(owner_id):
            await self._goals.delete(goal.id)
        # Every deck (flat, so a dangling-parent deck is still removed), with its
        # notes, cards, schedules and review logs.
        await self._library.delete_owner_library(owner_id)
        # Any review logs not reached through a deck (export reads them per owner,
        # so deletion matches), the uploaded media, and the usage counters.
        await self._review_logs.delete_for_owner(owner_id)
        await self._media.delete_for_owner(owner_id)
        await self._mock_exams.delete_for_owner(owner_id)
        await self._quotas.delete_for_owner(owner_id)
        await self._sessions.delete_for_user(owner_id)
        await self._users.delete(owner_id)
