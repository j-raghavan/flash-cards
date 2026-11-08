# SPDX-License-Identifier: AGPL-3.0-only
"""Request and response models for the HTTP API.

Email is accepted as a plain string and validated in the domain
(flashcards.domain.users.validate_email), so there is one source of truth for
what a valid address is and no extra email-parsing dependency.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from flashcards.domain.invites import Invite, InviteStatus, Role
from flashcards.domain.sessions import Session
from flashcards.domain.users import User


class SignupRequest(BaseModel):
    token: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class InviteCreateRequest(BaseModel):
    email: str | None = None
    role: Role = Role.MEMBER
    ttl_days: int = Field(default=7, gt=0, le=90)


class UserOut(BaseModel):
    id: str
    email: str
    role: Role

    @classmethod
    def from_user(cls, user: User) -> UserOut:
        return cls(id=user.id, email=user.email, role=user.role)


class CreatedInviteOut(BaseModel):
    id: str
    token: str  # shown exactly once; only its hash is stored
    role: Role
    email: str | None
    expires_at: datetime


class InviteOut(BaseModel):
    id: str
    role: Role
    email: str | None
    status: InviteStatus
    created_at: datetime
    expires_at: datetime

    @classmethod
    def from_invite(cls, invite: Invite, now: datetime) -> InviteOut:
        return cls(
            id=invite.id,
            role=invite.role,
            email=invite.email,
            status=invite.status(now),
            created_at=invite.created_at,
            expires_at=invite.expires_at,
        )


class SessionOut(BaseModel):
    id: str
    created_at: datetime
    expires_at: datetime
    current: bool

    @classmethod
    def from_session(cls, session: Session, *, current: bool) -> SessionOut:
        return cls(
            id=session.id,
            created_at=session.created_at,
            expires_at=session.expires_at,
            current=current,
        )
