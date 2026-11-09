# SPDX-License-Identifier: AGPL-3.0-only
"""Chat with a deck's source documents (F-AI-8).

A deck's sources are the stored documents and transcripts of the AI jobs that
generated into it. Each source is extracted and split into passages once, then
reused. A question retrieves the best passages lexically; the model answers from
those passages only and cites them by number. Passages and the question are sent
as data documents, never concatenated into the instructions (NF-SEC-6).
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from contextlib import aclosing

from flashcards.application.ai_budget import AiCallBudget
from flashcards.application.ai_prompts import chat_prompt
from flashcards.application.deck_service import DeckService
from flashcards.domain.ai import LlmDocument, StructuredRequest
from flashcards.domain.chat import (
    CHAT_MAX_OUTPUT_TOKENS,
    CHAT_SCHEMA,
    MAX_PASSAGES_PER_SOURCE,
    NOT_FOUND_ANSWER,
    ChatAnswer,
    Citation,
    NoChatSources,
    Passage,
    numbered_context,
    parse_chat_answer,
    rank_passages,
    split_passages,
)
from flashcards.domain.jobs import AiJob
from flashcards.domain.ports import (
    AiJobRepository,
    DocumentExtractor,
    LLMProvider,
    ObjectStore,
    PassageRepository,
)
from flashcards.domain.sources import (
    DEFAULT_MAX_UPLOAD_PAGES,
    clean_source_name,
    page_cap,
    source_name_key,
)
from flashcards.domain.usage import AiFeature

logger = logging.getLogger(__name__)

# Most pages of one source chat indexes for an admin, who has no page cap. Indexing
# reads each page's text inside the chat request, one page at a time on the single
# PyMuPDF slot, so this bounds how long one question can take (ADR 0022).
MAX_CHAT_INDEX_PAGES = 2_000


class ChatService:
    def __init__(
        self,
        decks: DeckService,
        jobs: AiJobRepository,
        store: ObjectStore,
        extractor: DocumentExtractor,
        passages: PassageRepository,
        llm: LLMProvider,
        budget: AiCallBudget,
        *,
        max_pages: int = DEFAULT_MAX_UPLOAD_PAGES,
    ) -> None:
        self._decks = decks
        self._jobs = jobs
        self._store = store
        self._extractor = extractor
        self._passages = passages
        self._llm = llm
        self._budget = budget
        self._max_pages = max_pages

    async def ask(
        self, *, owner_id: str, deck_id: str, question: str, admin: bool = False
    ) -> ChatAnswer:
        """Answer from the deck's sources; admin is the caller's role for this request.

        Indexing runs inside this request, so it is bounded for everyone: a member's
        sources by the page cap, an admin's (which have none, ADR 0022) by
        MAX_CHAT_INDEX_PAGES. A longer source is recorded as having no text.
        """
        # A deck's sources include its subdecks'. Raises DeckNotFound if the deck is
        # missing or not the caller's.
        deck_ids = await self._decks.subtree_ids(owner_id=owner_id, deck_id=deck_id)
        jobs = await self._jobs.list_for_owner(owner_id)
        cap = page_cap(self._max_pages, admin=admin)
        if cap is None:
            cap = max(self._max_pages, MAX_CHAT_INDEX_PAGES)
        await self._index(owner_id, deck_ids, jobs, cap)
        indexed = [p for d in deck_ids for p in await self._passages.list_for_deck(d)]
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
        # Raises SpendCapReached or AiCallLimitReached; releases the hold on failure.
        async with self._budget.charge(owner_id, request) as hold:
            result = await self._llm.generate_structured(request)
        await self._budget.record(owner_id, AiFeature.CHAT, result, hold)
        answer = parse_chat_answer(result.data, top)
        return dataclasses.replace(
            answer, citations=await self._named(jobs, answer.citations)
        )

    async def _named(
        self, jobs: list[AiJob], citations: tuple[Citation, ...]
    ) -> tuple[Citation, ...]:
        """Label each citation with its file's name, read only from the caller's own jobs."""
        own = {job.source_id for job in jobs}
        names: dict[str, str | None] = {}
        for file_id in dict.fromkeys(c.file_id for c in citations):
            raw = await self._store.get(source_name_key(file_id)) if file_id in own else None
            names[file_id] = (
                None if raw is None else clean_source_name(raw.decode("utf-8", "replace"))
            )
        return tuple(dataclasses.replace(c, file_name=names[c.file_id]) for c in citations)

    async def _index(
        self, owner_id: str, deck_ids: list[str], jobs: list[AiJob], max_pages: int
    ) -> None:
        # Each source is indexed under the deck its job generated into, so deleting
        # that deck later removes exactly its passages.
        wanted = set(deck_ids)
        done = {(d, f) for d in deck_ids for f in await self._passages.indexed_files(d)}
        for job in jobs:
            key = (job.draft_deck_id, job.source_id)
            if job.draft_deck_id not in wanted or key in done:
                continue
            data = await self._store.get(job.source_id)
            if data is None:
                continue  # the source was removed; nothing to index
            await self._passages.add_many(
                await self._extract(owner_id, job.draft_deck_id, job.source_id, data, max_pages)
            )
            done.add(key)

    async def _extract(
        self, owner_id: str, deck_id: str, file_id: str, data: bytes, max_pages: int
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
            # Retrieval is text-only, so text-less pages are not rasterized at all;
            # aclosing closes the document at once when the passage bound stops early.
            async with aclosing(
                self._extractor.extract(
                    data, file_id=file_id, max_pages=max_pages, text_only=True
                )
            ) as pages:
                async for page in pages:
                    # Image-only pages have no text to retrieve (text_only leaves them
                    # out); card generation reads them with the vision model.
                    for ordinal, text in enumerate(split_passages(page.text or "")):
                        out.append(passage(page.ref.page, ordinal, text))
                    if len(out) >= MAX_PASSAGES_PER_SOURCE:
                        # Bound what one source can add to the index and to every ranking.
                        out = out[:MAX_PASSAGES_PER_SOURCE]
                        break
        except Exception:
            # Any unreadable source (encrypted PDF, corrupt file, bad envelope) must
            # not break chat for the whole deck: record it as having no text.
            logger.warning("could not index chat source %s", file_id, exc_info=True)
            out = []
        # Always record the file, even with no text, so it is not re-extracted.
        return out or [passage(0, 0, "")]
