# SPDX-License-Identifier: AGPL-3.0-only
"""SQLAlchemy async engine, table models and row/domain mappers.

The ORM rows live here in the adapter layer; the domain entities stay free of
SQLAlchemy. Repositories (adapters.sql) translate between the two.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from flashcards.domain.invites import Invite, Role
from flashcards.domain.sessions import Session
from flashcards.domain.users import User


class Base(DeclarativeBase):
    pass


class InviteRow(Base):
    __tablename__ = "invites"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    role: Mapped[str] = mapped_column(String)
    created_by: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def invite_to_row(invite: Invite) -> InviteRow:
    return InviteRow(
        id=invite.id,
        token_hash=invite.token_hash,
        role=invite.role.value,
        created_by=invite.created_by,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        email=invite.email,
        revoked_at=invite.revoked_at,
        redeemed_at=invite.redeemed_at,
    )


def invite_from_row(row: InviteRow) -> Invite:
    return Invite(
        id=row.id,
        token_hash=row.token_hash,
        role=Role(row.role),
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
        email=row.email,
        revoked_at=row.revoked_at,
        redeemed_at=row.redeemed_at,
    )


def user_to_row(user: User) -> UserRow:
    return UserRow(
        id=user.id,
        email=user.email,
        password_hash=user.password_hash,
        role=user.role.value,
        created_at=user.created_at,
        email_verified_at=user.email_verified_at,
        suspended_at=user.suspended_at,
    )


def user_from_row(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        role=Role(row.role),
        created_at=row.created_at,
        email_verified_at=row.email_verified_at,
        suspended_at=row.suspended_at,
    )


def session_to_row(session: Session) -> SessionRow:
    return SessionRow(
        id=session.id,
        user_id=session.user_id,
        token_hash=session.token_hash,
        created_at=session.created_at,
        expires_at=session.expires_at,
        revoked_at=session.revoked_at,
    )


def session_from_row(row: SessionRow) -> Session:
    return Session(
        id=row.id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


def normalize_async_url(database_url: str) -> str:
    """Coerce a plain Postgres URL to the asyncpg driver.

    Hosted providers hand out ``postgresql://`` (or ``postgres://``); the async
    engine needs ``postgresql+asyncpg://``. A URL that already names a driver is
    left untouched.
    """
    if database_url.startswith("postgresql+"):
        return database_url
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+asyncpg://" + database_url[len(prefix) :]
    return database_url


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(normalize_async_url(database_url), pool_pre_ping=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
