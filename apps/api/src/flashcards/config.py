# SPDX-License-Identifier: AGPL-3.0-only
"""Application settings, read from environment variables (prefix FLASHCARDS_)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLASHCARDS_", env_file=".env", extra="ignore")

    environment: str = "development"
    invite_only: bool = True
    max_upload_pages: int = 20
    # Session cookie is marked Secure by default; turn off only for HTTP in tests.
    session_cookie_secure: bool = True
    # Dev convenience: seed one admin invite at startup and log its token, so a
    # local instance is usable before persistence/first-admin bootstrap exist.
    # Ignored in production and in SQL mode.
    dev_bootstrap_invite: bool = False
    # SQLAlchemy async URL (postgresql+asyncpg://...). Unset means in-memory mode.
    database_url: str | None = None
