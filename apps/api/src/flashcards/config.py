# SPDX-License-Identifier: AGPL-3.0-only
"""Application settings, read from environment variables (prefix FLASHCARDS_)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLASHCARDS_", env_file=".env", extra="ignore")

    environment: str = "development"
    invite_only: bool = True
    max_upload_pages: int = 20
