# SPDX-License-Identifier: AGPL-3.0-only
"""Account-wide data export (F-IMP-5): everything a user owns, in one structure."""

from __future__ import annotations

from dataclasses import dataclass

from flashcards.domain.decks import Deck
from flashcards.domain.goals import Goal
from flashcards.domain.notes import Card, Note
from flashcards.domain.ports import (
    CardRepository,
    DeckRepository,
    GoalRepository,
    NoteRepository,
    ReviewLogRepository,
    ScheduleRepository,
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
        decks: DeckRepository,
        notes: NoteRepository,
        cards: CardRepository,
        schedules: ScheduleRepository,
        review_logs: ReviewLogRepository,
        goals: GoalRepository,
    ) -> None:
        self._decks = decks
        self._notes = notes
        self._cards = cards
        self._schedules = schedules
        self._review_logs = review_logs
        self._goals = goals

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
