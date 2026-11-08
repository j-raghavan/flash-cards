# SPDX-License-Identifier: AGPL-3.0-only
"""Chat with a deck's source documents (F-AI-8).

A deck's sources are the stored documents and transcripts of the AI jobs that
generated into it. Each source is extracted and split into passages once, then
reused. A question retrieves the best passages lexically; the model answers from
those passages only and cites them by number. Passages and the question are sent
as data documents, never concatenated into the instructions (NF-SEC-6).
"""

from __future__ import annotations

import uuid

from flashcards.application.ai_prompts import chat_prompt
from flashcards.application.deck_service import DeckService
from flashcards.domain.ai import LlmDocument, StructuredRequest
from flashcards.domain.chat import (
    CHAT_MAX_OUTPUT_TOKENS,
    CHAT_SCHEMA,
    NOT_FOUND_ANSWER,
    ChatAnswer,
    NoChatSources,
    Passage,
    numbered_context,
    parse_chat_answer,
    rank_passages,
    split_passages,
)
from flashcards.domain.ports import (
    AiJobRepository,
    DocumentExtractor,
    LLMProvider,
    ObjectStore,
    PassageRepository,
)
from flashcards.domain.sources import PageKind, TooManyPages, UnsupportedMediaType
from flashcards.domain.transcripts import InvalidTranscript

# A stored source that cannot be read is indexed with an empty marker passage, so
# it is not re-extracted on every question.
_UNREADABLE = (TooManyPages, UnsupportedMediaType, InvalidTranscript)


class ChatService:
    def __init__(
        self,
        decks: DeckService,
        jobs: AiJobRepository,
        store: ObjectStore,
        extractor: DocumentExtractor,
        passages: PassageRepository,
        llm: LLMProvider,
        *,
        max_pages: int = 20,
    ) -> None:
        self._decks = decks
        self._jobs = jobs
        self._store = store
        self._extractor = extractor
        self._passages = passages
        self._llm = llm
        self._max_pages = max_pages

    async def ask(self, *, owner_id: str, deck_id: str, question: str) -> ChatAnswer:
        # Raises DeckNotFound if the deck is missing or not the caller's.
        await self._decks.get(owner_id=owner_id, deck_id=deck_id)
        await self._index(owner_id, deck_id)
        indexed = await self._passages.list_for_deck(deck_id)
        if not any(p.text.strip() for p in indexed):
            raise NoChatSources(deck_id)
        top = rank_passages(indexed, question)
        if not top:
            # Nothing in the sources shares a term with the question: answer without
            # spending tokens rather than letting the model guess.
            return ChatAnswer(answer=NOT_FOUND_ANSWER, citations=())
        request = StructuredRequest(
            instructions=chat_prompt(),
            documents=(
                LlmDocument(media_type="text/plain", data=numbered_context(top).encode("utf-8")),
                LlmDocument(media_type="text/plain", data=question.encode("utf-8")),
            ),
            schema=CHAT_SCHEMA,
            max_output_tokens=CHAT_MAX_OUTPUT_TOKENS,
        )
        result = await self._llm.generate_structured(request)
        return parse_chat_answer(result.data, top)

    async def _index(self, owner_id: str, deck_id: str) -> None:
        done = await self._passages.indexed_files(deck_id)
        for job in await self._jobs.list_for_owner(owner_id):
            if job.draft_deck_id != deck_id or job.source_id in done:
                continue
            data = await self._store.get(job.source_id)
            if data is None:
                continue  # the source was removed; nothing to index
            await self._passages.add_many(
                await self._extract(owner_id, deck_id, job.source_id, data)
            )
            done.add(job.source_id)

    async def _extract(
        self, owner_id: str, deck_id: str, file_id: str, data: bytes
    ) -> list[Passage]:
        def passage(page: int, ordinal: int, text: str) -> Passage:
            return Passage(
                id=str(uuid.uuid4()),
                owner_id=owner_id,
                deck_id=deck_id,
                file_id=file_id,
                page=page,
                ordinal=ordinal,
                text=text,
            )

        out: list[Passage] = []
        try:
            async for page in self._extractor.extract(
                data, file_id=file_id, max_pages=self._max_pages
            ):
                # Image-only pages have no text to retrieve; card generation reads them
                # with the vision model, but chat retrieval is text-only.
                if page.kind is PageKind.TEXT and page.text:
                    for ordinal, text in enumerate(split_passages(page.text)):
                        out.append(passage(page.ref.page, ordinal, text))
        except _UNREADABLE:
            out = []
        # Always record the file, even with no text, so it is not re-extracted.
        return out or [passage(0, 0, "")]
