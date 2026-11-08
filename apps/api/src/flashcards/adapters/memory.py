# SPDX-License-Identifier: AGPL-3.0-only
"""In-memory adapters for tests and local experiments. Not for production.

The methods are async to satisfy the repository ports. Each runs to completion
without awaiting, so on a single event loop it is effectively atomic, which is
what the invite single-use guarantee relies on here.
"""

from __future__ import annotations

from datetime import UTC, datetime

from flashcards.domain.decks import Deck
from flashcards.domain.invites import Invite
from flashcards.domain.notes import Card, Note
from flashcards.domain.scheduling import CardSchedule, ReviewLog
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

    async def delete(self, note_id: str) -> None:
        self._by_id.pop(note_id, None)


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


class InMemoryScheduleRepository:
    def __init__(self) -> None:
        self._by_card: dict[str, CardSchedule] = {}

    async def get(self, card_id: str) -> CardSchedule | None:
        return self._by_card.get(card_id)

    async def upsert(self, schedule: CardSchedule) -> None:
        self._by_card[schedule.card_id] = schedule


class InMemoryReviewLogRepository:
    def __init__(self) -> None:
        self._logs: list[ReviewLog] = []

    async def append(self, log: ReviewLog) -> None:
        self._logs.append(log)

    async def exists_key(self, owner_id: str, idempotency_key: str) -> bool:
        return any(
            log.owner_id == owner_id and log.idempotency_key == idempotency_key
            for log in self._logs
        )
