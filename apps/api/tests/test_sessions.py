# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flashcards.domain.sessions import Session, SessionStatus

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def make_session(**overrides: object) -> Session:
    base: dict[str, object] = {
        "id": "sess-1",
        "user_id": "user-1",
        "token_hash": "hash-1",
        "created_at": NOW,
        "expires_at": NOW + timedelta(days=14),
    }
    base.update(overrides)
    return Session(**base)  # type: ignore[arg-type]


def test_fresh_session_is_active_and_valid() -> None:
    session = make_session()
    assert session.status(NOW) is SessionStatus.ACTIVE
    assert session.is_valid(NOW) is True


def test_session_expires_at_its_deadline() -> None:
    session = make_session()
    assert session.status(NOW + timedelta(days=14)) is SessionStatus.EXPIRED
    assert session.is_valid(NOW + timedelta(days=14)) is False


def test_revoked_session_is_invalid_even_before_expiry() -> None:
    session = make_session().with_revoked(NOW + timedelta(days=1))
    assert session.status(NOW + timedelta(days=2)) is SessionStatus.REVOKED
    assert session.is_valid(NOW + timedelta(days=2)) is False
