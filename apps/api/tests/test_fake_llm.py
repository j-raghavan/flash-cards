# SPDX-License-Identifier: AGPL-3.0-only
"""The test-only fake LLM provider (ADR 0021): what it answers, and that it can
never be selected in production or on a persistent (SQL) instance."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from flashcards.adapters.llm import FAKE_MODEL, FakeLLMProvider
from flashcards.api.app import _llm_from_settings, create_app
from flashcards.config import Settings
from flashcards.domain.ai import (
    DRAFT_CARDS_SCHEMA,
    LlmError,
    StructuredRequest,
    parse_draft_cards,
)
from flashcards.domain.summary import SUMMARY_SCHEMA


class _HermeticSettings(Settings):
    """Ignores the developer's environment and .env file."""

    model_config = SettingsConfigDict(
        env_prefix="FLASHCARDS_TEST_UNSET_", env_file=None, extra="ignore"
    )


def _request(schema: Mapping[str, object] = DRAFT_CARDS_SCHEMA) -> StructuredRequest:
    return StructuredRequest(instructions="x", documents=(), schema=schema, max_output_tokens=10)


async def test_fake_returns_fixed_drafts_that_pass_the_domain_parser() -> None:
    result = await FakeLLMProvider().generate_structured(_request())
    drafts = parse_draft_cards(result.data, source_page=1)
    assert [d.note_type.value for d in drafts] == ["basic", "cloze"]
    assert result.model == FAKE_MODEL
    assert (result.usage.input_tokens, result.usage.output_tokens) == (0, 0)


async def test_fake_refuses_every_other_ai_feature() -> None:
    with pytest.raises(LlmError):
        await FakeLLMProvider().generate_structured(_request(SUMMARY_SCHEMA))


@pytest.mark.parametrize("environment", ["development", "test"])
def test_fake_is_selected_in_development_and_test(environment: str) -> None:
    settings = _HermeticSettings(llm_provider="fake", environment=environment)
    assert isinstance(_llm_from_settings(settings), FakeLLMProvider)


@pytest.mark.parametrize("environment", ["production", "Production", "staging", ""])
def test_fake_is_refused_outside_development_and_test(environment: str) -> None:
    with pytest.raises(ValidationError, match="tests only"):
        _HermeticSettings(llm_provider="fake", environment=environment)


def test_fake_is_refused_in_sql_mode() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        _HermeticSettings(
            llm_provider="fake",
            environment="development",
            database_url="postgresql+asyncpg://u:p@localhost/db",
        )


def test_fake_is_refused_from_the_environment_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLASHCARDS_LLM_PROVIDER", "fake")
    monkeypatch.setenv("FLASHCARDS_ENVIRONMENT", "production")
    with pytest.raises(ValidationError, match="tests only"):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("environment", "database_url"),
    [("production", None), ("development", "postgresql+asyncpg://u:p@localhost/db")],
)
def test_unvalidated_settings_cannot_slip_the_fake_through(
    environment: str, database_url: str | None
) -> None:
    # model_construct skips validation; the app checks again before wiring it.
    settings = _HermeticSettings.model_construct(
        llm_provider="fake", environment=environment, database_url=database_url
    )
    with pytest.raises(RuntimeError, match="FLASHCARDS_LLM_PROVIDER=fake"):
        create_app(settings)
