# SPDX-License-Identifier: AGPL-3.0-only
"""Application settings, read from environment variables (prefix FLASHCARDS_)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from flashcards.domain.quotas import MAX_MONTHLY_PAGES
from flashcards.domain.sources import DEFAULT_MAX_UPLOAD_PAGES
from flashcards.domain.transcripts import parse_language_preference

# The only environments in which the test-only "fake" LLM provider may run
# (ADR 0021). Anything else, "production" included, refuses it at startup.
FAKE_LLM_ENVIRONMENTS = frozenset({"development", "test"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLASHCARDS_", env_file=".env", extra="ignore")

    environment: str = "development"
    # F-AUTH-7: with false, anyone can create a member account at POST /auth/register
    # (throttled per client, verification email sent). Invites keep working either way.
    invite_only: bool = True
    # Most pages (or transcript chunks) an AI source document may have, for card
    # generation and document chat.
    max_upload_pages: int = Field(default=DEFAULT_MAX_UPLOAD_PAGES, ge=1)
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
    # A Literal, so a typo (e.g. "openai") fails at startup instead of silently
    # falling back to a provider that refuses every request.
    # "fake" is for the end-to-end tests only: it returns fixed draft cards and is
    # refused unless the environment is development or test and the server runs
    # in in-memory mode (ADR 0021).
    llm_provider: Literal["anthropic", "openai_compatible", "fake"] = "anthropic"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    # OpenAI-compatible endpoint (LM Studio, Ollama, vLLM, ...). base_url points at
    # the API root, e.g. http://localhost:1234/v1. The key is optional; many local
    # servers ignore it. Used only when llm_provider == "openai_compatible".
    openai_base_url: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "local-model"
    # Seconds to wait on the endpoint. Local models on CPU can be slow to answer.
    openai_timeout_seconds: float = 300.0
    # The request field that caps the answer's length. OpenAI's reasoning models
    # (GPT-5, the o-series) refuse "max_tokens" and need "max_completion_tokens".
    openai_output_limit_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    # Sent as reasoning_effort when set, for reasoning models only; which values a
    # model accepts varies. Their reasoning counts against the output budget, so a
    # low effort leaves room for the answer. Unset sends nothing, which other
    # servers expect.
    openai_reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    # Synchronous AI requests (grading, summaries, mock exams, chat) running at once.
    # Keep it below the database pool size: each holds a connection while it waits
    # on the model.
    max_concurrent_ai_calls: int = 4
    # What the provider charges, in US dollars per million input and output tokens.
    # Every AI call's cost is recorded at these prices (F-ADMIN-4), and the monthly
    # spend cap compares against that cost. Zero (the default) records no cost.
    ai_input_usd_per_mtok: float = Field(default=0.0, ge=0)
    ai_output_usd_per_mtok: float = Field(default=0.0, ge=0)
    # Initial quotas, used until an admin sets them at PUT /admin/quotas.
    # Unset (None) means members have no monthly page limit (ADR 0022); set a number
    # to meter them. The spend cap still applies either way.
    ai_default_monthly_pages: int | None = Field(default=None, ge=0, le=MAX_MONTHLY_PAGES)
    ai_monthly_spend_cap_usd: float | None = Field(default=None, ge=0)
    # The AI data-use disclosure (F-LEGAL-2): the provider's display name and terms
    # link. Known providers have defaults; a self-hosted endpoint should set both.
    ai_provider_name: str | None = None
    ai_provider_terms_url: str | None = None
    # Automatic YouTube transcript retrieval (F-AI-2, ADR 0020). Off by default: when
    # on, this server contacts youtube.com for every YouTube link a user submits.
    # Off, a YouTube link answers 422 and the user pastes the transcript instead.
    youtube_transcripts_enabled: bool = False
    # Comma-separated language tags, most preferred first ("de,en"). A video with
    # none of them falls back to whatever transcript it has.
    youtube_transcript_languages: str = "en"
    # Connect and read timeout for each request to YouTube; one fetch as a whole is
    # cut off after three times this.
    youtube_transcript_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    # Legal pages (F-LEGAL-1): a directory holding privacy.md, terms.md and
    # imprint.md, served at GET /legal/{page}. A missing file falls back to a
    # template that says the operator has not filled the page in yet.
    legal_dir: str | None = None
    # Where the web client lives; links in emails point here. Its host is also the
    # WebAuthn relying party id, and its origin the only one passkeys answer for.
    public_base_url: str = "http://localhost:5173"
    # Per-address rate limit for POST /auth/* and /shared/* (ADR 0019), per process.
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = Field(default=20, ge=1)
    rate_limit_shared_per_minute: int = Field(default=60, ge=1)
    # How many reverse proxies append to X-Forwarded-For in front of the API. 0 (the
    # default) ignores the header and limits by the connection's peer address.
    trusted_proxy_count: int = Field(default=0, ge=0, le=10)
    # Comma-separated Fernet keys that encrypt authenticator-app (TOTP) secrets at
    # rest, newest first (ADR 0018). Without one, SQL mode offers no authenticator
    # apps (passkeys still work); in-memory mode uses a throwaway key.
    totp_encryption_keys: str | None = None
    # Outgoing mail. Without smtp_host, mail is not sent: in development it is
    # logged so the links can be followed; elsewhere only its subject is logged.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_sender: str = "Flash-Cards <no-reply@localhost>"
    # "starttls" (port 587), "ssl" (port 465) or "none" (a local relay only; no login).
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"

    @field_validator("youtube_transcript_languages")
    @classmethod
    def _languages(cls, value: str) -> str:
        parse_language_preference(value)  # raises ValueError with the reason
        return value

    @field_validator("ai_default_monthly_pages", mode="before")
    @classmethod
    def _blank_page_limit_is_unlimited(cls, value: object) -> object:
        # An empty env value (FLASHCARDS_AI_DEFAULT_MONTHLY_PAGES=) means unlimited,
        # so documenting the variable cannot crash startup (ADR 0027).
        return None if isinstance(value, str) and value.strip() == "" else value

    @model_validator(mode="after")
    def _fake_llm_only_in_tests(self) -> Settings:
        if self.llm_provider == "fake":
            refusal = fake_llm_refusal(self.environment, self.database_url)
            if refusal is not None:
                raise ValueError(refusal)
        return self


def fake_llm_refusal(environment: str, database_url: str | None) -> str | None:
    """Why the fake LLM provider may not run here, or None when it may (ADR 0021)."""
    if environment not in FAKE_LLM_ENVIRONMENTS:
        return (
            f"FLASHCARDS_LLM_PROVIDER=fake is for tests only and is refused when "
            f"FLASHCARDS_ENVIRONMENT is {environment!r}."
        )
    if database_url:
        return "FLASHCARDS_LLM_PROVIDER=fake is refused when FLASHCARDS_DATABASE_URL is set."
    return None
