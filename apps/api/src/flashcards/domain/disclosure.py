# SPDX-License-Identifier: AGPL-3.0-only
"""The AI data-use disclosure shown to users (F-LEGAL-2).

States what this software does with user content when an AI feature runs, and
names the provider the operator configured with a link to its terms, which
govern how the provider handles the data it receives.
"""

from __future__ import annotations

from dataclasses import dataclass

TRAINING_STATEMENT = (
    "Flash-Cards never uses your content to train AI models, and never shares it "
    "for that purpose."
)
PROCESSING_STATEMENT = (
    "When you use an AI feature, only the material that feature needs (for example "
    "the pages of a document, a card and your answer, or your question with the "
    "passages that answer it) is sent to the AI provider configured by the operator "
    "of this instance, to produce that result. The provider's terms govern how it "
    "handles that data."
)
NO_PROVIDER_STATEMENT = (
    "No AI provider is configured on this instance, so AI features are off and no "
    "content is sent anywhere."
)

# Default terms pages for providers whose terms are known; an operator can
# override the link, and must supply one for a self-hosted endpoint.
KNOWN_PROVIDERS: dict[str, tuple[str, str]] = {
    "anthropic": ("Anthropic", "https://www.anthropic.com/legal/commercial-terms"),
    "openai_compatible": ("OpenAI-compatible endpoint", ""),
}


@dataclass(frozen=True, slots=True)
class AiProviderInfo:
    key: str  # the configured backend, e.g. "anthropic"
    name: str  # a display name
    terms_url: str | None


@dataclass(frozen=True, slots=True)
class AiDisclosure:
    ai_enabled: bool
    trains_on_user_content: bool
    statements: tuple[str, ...]
    provider: AiProviderInfo | None


def ai_disclosure(
    *,
    provider_key: str | None,
    name: str | None = None,
    terms_url: str | None = None,
) -> AiDisclosure:
    """The disclosure for the configured provider; provider_key None means AI is off.

    `name` and `terms_url` override the defaults for a known provider.
    """
    if provider_key is None:
        return AiDisclosure(
            ai_enabled=False,
            trains_on_user_content=False,
            statements=(TRAINING_STATEMENT, NO_PROVIDER_STATEMENT),
            provider=None,
        )
    default_name, default_terms = KNOWN_PROVIDERS.get(provider_key, (provider_key, ""))
    return AiDisclosure(
        ai_enabled=True,
        trains_on_user_content=False,
        statements=(TRAINING_STATEMENT, PROCESSING_STATEMENT),
        provider=AiProviderInfo(
            key=provider_key,
            name=name or default_name,
            terms_url=terms_url or default_terms or None,
        ),
    )
