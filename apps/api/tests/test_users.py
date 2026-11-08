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
    "good", ["first.last+tag@example.co.uk", "o'neil@mail.example.org", "x_1-2@a-b.io"]
)
def test_validate_email_accepts_ordinary_addresses(good: str) -> None:
    assert validate_email(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "no-at-sign.com",
        "@example.com",
        "person@nodot",
        "person@.leadingdot.com",
        "person@trailingdot.",
        "person@double..dot.com",
        # Could add a recipient or a header to an email.
        "a@example.com,b@example.com",
        "a@example.com b@example.com",
        "a@example.com\r\nBcc: b@example.com",
        "Name <a@example.com>",
        '"quoted"@example.com',
        "a@b@example.com",
        # Out of the plain address shape or its length limits.
        ".dot@example.com",
        "dot.@example.com",
        "a..b@example.com",
        "person@-dash.com",
        "person@example.c",
        "person@example.123",
        "p\u00e9rson@example.com",
        "a" * 65 + "@example.com",
        "a@" + "b" * 64 + ".com",
        "a@" + ("b" * 60 + ".") * 5 + "com",  # 310 characters
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
