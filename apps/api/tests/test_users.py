# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from flashcards.domain.invites import Role
from flashcards.domain.users import (
    InvalidEmail,
    User,
    UserStatus,
    WeakPassword,
    validate_email,
    validate_password,
)

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def make_user(**overrides: object) -> User:
    base: dict[str, object] = {
        "id": "user-1",
        "email": "person@example.com",
        "password_hash": "hashed",
        "role": Role.MEMBER,
        "created_at": NOW,
    }
    base.update(overrides)
    return User(**base)  # type: ignore[arg-type]


def test_validate_email_normalizes_case_and_whitespace() -> None:
    assert validate_email("  Person@Example.COM ") == "person@example.com"


@pytest.mark.parametrize(
    "bad",
    [
        "no-at-sign.com",
        "@example.com",
        "person@nodot",
        "person@.leadingdot.com",
        "person@trailingdot.",
        "person@double..dot.com",
    ],
)
def test_validate_email_rejects_malformed(bad: str) -> None:
    with pytest.raises(InvalidEmail):
        validate_email(bad)


def test_validate_password_accepts_long_enough() -> None:
    validate_password("correct-horse-battery")


def test_validate_password_rejects_short() -> None:
    with pytest.raises(WeakPassword):
        validate_password("short")


def test_new_user_is_active_and_unverified() -> None:
    user = make_user()
    assert user.status() is UserStatus.ACTIVE
    assert user.is_verified is False


def test_with_verified_sets_timestamp() -> None:
    user = make_user().with_verified(NOW)
    assert user.is_verified is True
    assert user.email_verified_at == NOW


def test_with_suspended_changes_status() -> None:
    user = make_user().with_suspended(NOW)
    assert user.status() is UserStatus.SUSPENDED
    assert user.suspended_at == NOW
