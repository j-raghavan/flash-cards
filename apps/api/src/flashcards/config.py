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
    # AI generation backend selector: "anthropic" (default) or "openai_compatible".
    # With no backend configured the server wires a provider that refuses, so
    # generation jobs fail cleanly instead of pretending to work.
    llm_provider: str = "anthropic"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    # OpenAI-compatible endpoint (LM Studio, Ollama, vLLM, ...). base_url points at
    # the API root, e.g. http://localhost:1234/v1. The key is optional; many local
    # servers ignore it. Used only when llm_provider == "openai_compatible".
    openai_base_url: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "local-model"
    # Synchronous AI requests (grading, summaries, mock exams, chat) running at once.
    # Keep it below the database pool size: each holds a connection while it waits
    # on the model.
    max_concurrent_ai_calls: int = 4
