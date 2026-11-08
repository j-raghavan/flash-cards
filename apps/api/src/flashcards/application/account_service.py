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
from flashcards.domain.notes import Card, Note
from flashcards.domain.ports import (
    AiJobRepository,
    CardRepository,
    DeckRepository,
    DraftCardRepository,
    GoalRepository,
    NoteRepository,
    ObjectStore,
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
        )

    async def delete(self, *, user: User) -> None:
        owner_id = user.id
        # AI jobs, their drafts (cascade) and their stored source files.
        for job in await self._ai_jobs.list_for_owner(owner_id):
            await self._object_store.delete(job.source_id)
            await self._draft_cards.delete_for_job(job.id)
            await self._ai_jobs.delete(job.id)
        for goal in await self._goals.list_for_owner(owner_id):
            await self._goals.delete(goal.id)
        # Delete top-level decks; each subtree takes its nested decks, notes, cards,
        # schedules and review logs with it.
        for deck in await self._decks.list_for_owner(owner_id):
            if deck.parent_id is None:
                await self._library.delete_deck(owner_id=owner_id, deck_id=deck.id)
        await self._sessions.delete_for_user(owner_id)
        await self._users.delete(owner_id)
