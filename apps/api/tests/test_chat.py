# SPDX-License-Identifier: AGPL-3.0-only
"""Document chat: retrieval, citation parsing and the service (in-memory)."""

from __future__ import annotations

from datetime import UTC, datetime

import pymupdf
import pytest

from flashcards.adapters.extraction import PyMuPDFExtractor
from flashcards.adapters.memory import (
    InMemoryAiJobRepository,
    InMemoryDeckRepository,
    InMemoryObjectStore,
    InMemoryPassageRepository,
    InMemoryQuotaRepository,
    InMemoryQuotaSettingsRepository,
    InMemoryUsageRepository,
    NullUnitOfWork,
    SystemClock,
)
from flashcards.adapters.transcripts import TranscriptAwareExtractor
from flashcards.application.ai_budget import AiCallBudget
from flashcards.application.ai_spend import AiSpend
from flashcards.application.chat_service import ChatService
from flashcards.application.deck_service import DeckService
from flashcards.domain.ai import StructuredRequest, StructuredResult, Thoroughness, TokenUsage
from flashcards.domain.chat import (
    NOT_FOUND_ANSWER,
    Citation,
    InvalidChatAnswer,
    NoChatSources,
    Passage,
    numbered_context,
    parse_chat_answer,
    rank_passages,
    split_passages,
    tokenize,
)
from flashcards.domain.decks import DeckNotFound
from flashcards.domain.jobs import new_generation_job
from flashcards.domain.ports import Clock
from flashcards.domain.quotas import InstanceQuotas
from flashcards.domain.transcripts import encode_transcript, pasted_transcript
from flashcards.domain.usage import TokenPrices

NOW = datetime(2026, 11, 8, tzinfo=UTC)

def _spend(clock: Clock) -> AiSpend:
    return AiSpend(
        InMemoryUsageRepository(),
        InMemoryQuotaSettingsRepository(),
        clock,
        provider="test",
        prices=TokenPrices(),
        defaults=InstanceQuotas(),
    )



def _p(text: str, *, file_id: str = "f", page: int = 1, ordinal: int = 0) -> Passage:
    return Passage(
        id=f"{file_id}-{page}-{ordinal}",
        owner_id="alice",
        deck_id="d",
        file_id=file_id,
        page=page,
        ordinal=ordinal,
        text=text,
    )


def test_tokenize_casefolds_and_drops_stopwords() -> None:
    assert tokenize("What is the Krebs Cycle? Étape 2") == ["krebs", "cycle", "étape", "2"]


def test_split_passages_groups_paragraphs_and_splits_long_ones() -> None:
    text = "aaa\n\nbbb\n\n" + "c" * 12 + "\n\n   \n\nd"
    assert split_passages(text, max_chars=8) == ["aaa bbb", "cccccccc", "cccc d"]
    assert split_passages("   ") == []


def test_rank_passages_orders_by_relevance_then_position() -> None:
    cells = _p("mitochondria produce ATP in the cell", page=1)
    plants = _p("chloroplasts capture light in plants", page=2)
    both = _p("mitochondria mitochondria ATP ATP energy", page=3)
    tie_a = _p("osmosis", page=4)
    tie_b = _p("osmosis", page=5)
    question = "Where is ATP made by mitochondria?"
    ranked = rank_passages([cells, plants, both, tie_a, tie_b], question)
    assert ranked == [both, cells]
    assert rank_passages([tie_a, tie_b], "osmosis") == [tie_a, tie_b]
    assert rank_passages([cells, both], "mitochondria", k=1) == [both]


def test_rank_passages_returns_nothing_without_overlap() -> None:
    assert rank_passages([_p("photosynthesis")], "volcano") == []
    assert rank_passages([_p("photosynthesis")], "what is the") == []  # stopwords only
    assert rank_passages([_p("   "), _p("!!!")], "volcano") == []
    assert rank_passages([], "anything") == []


def test_numbered_context_labels_each_passage() -> None:
    text = numbered_context([_p("one", file_id="a", page=2), _p("two", file_id="b", page=7)])
    assert text == "[1] (source a, page 2)\none\n\n[2] (source b, page 7)\ntwo"


def test_parse_chat_answer_maps_numbers_to_citations() -> None:
    passages = [_p("x", file_id="a", page=1), _p("y", file_id="a", page=1, ordinal=1), _p("z")]
    answer = parse_chat_answer(
        {"answer": "  The answer.  ", "citations": [2, 1, 9, 0, True, "3", 3]}, passages
    )
    # 2 and 1 point at the same file and page, so they collapse to one citation.
    assert answer.answer == "The answer."
    assert answer.citations == (Citation("a", 1), Citation("f", 1))


@pytest.mark.parametrize(
    "raw",
    [{"citations": []}, {"answer": "  ", "citations": []}, {"answer": "x", "citations": "1"}],
)
def test_parse_chat_answer_rejects_malformed(raw: dict[str, object]) -> None:
    with pytest.raises(InvalidChatAnswer):
        parse_chat_answer(raw, [_p("x")])


class _FakeLLM:
    def __init__(self, data: dict[str, object] | None = None) -> None:
        self._data = data or {"answer": "ATP is made in mitochondria.", "citations": [1]}
        self.requests: list[StructuredRequest] = []

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        self.requests.append(request)
        return StructuredResult(data=self._data, usage=TokenUsage(1, 1), model="fake")


class _Rig:
    def __init__(self, llm: _FakeLLM) -> None:
        clock = SystemClock()
        self.decks = DeckService(InMemoryDeckRepository(), clock)
        self.jobs = InMemoryAiJobRepository()
        self.store = InMemoryObjectStore()
        self.passages = InMemoryPassageRepository()
        self.service = ChatService(
            self.decks,
            self.jobs,
            self.store,
            TranscriptAwareExtractor(PyMuPDFExtractor()),
            self.passages,
            llm,
            AiCallBudget(
                InMemoryQuotaRepository(limit=100), clock, _spend(clock), NullUnitOfWork()
            ),
        )

    async def deck(self, owner: str = "alice") -> str:
        deck_id: str = (await self.decks.create(owner_id=owner, name="Bio")).id
        return deck_id

    async def source(self, deck_id: str, data: bytes, owner: str = "alice") -> str:
        job = new_generation_job(
            owner_id=owner,
            source_id=f"src-{len(await self.jobs.list_for_owner(owner))}",
            draft_deck_id=deck_id,
            thoroughness=Thoroughness.STANDARD,
            now=NOW,
        )
        await self.jobs.add(job)
        await self.store.put(job.source_id, data, "application/octet-stream")
        return job.source_id


def _text_source(text: str) -> bytes:
    return encode_transcript(pasted_transcript(text))


async def test_ask_answers_with_citations_and_indexes_once() -> None:
    llm = _FakeLLM()
    rig = _Rig(llm)
    deck_id = await rig.deck()
    source = await rig.source(deck_id, _text_source("Mitochondria make ATP for the cell."))

    answer = await rig.service.ask(owner_id="alice", deck_id=deck_id, question="Who makes ATP?")

    assert answer.answer == "ATP is made in mitochondria."
    assert answer.citations == (Citation(source, 1),)
    request = llm.requests[0]
    # Passages and question are separate data documents, not in the instructions.
    assert request.documents[0].data.startswith(b"[1] (source " + source.encode())
    assert request.documents[1].data == b"Who makes ATP?"
    assert "ATP" not in request.instructions

    before = len(await rig.passages.list_for_deck(deck_id))
    await rig.service.ask(owner_id="alice", deck_id=deck_id, question="ATP?")
    assert len(await rig.passages.list_for_deck(deck_id)) == before  # not re-indexed


async def test_ask_only_indexes_the_decks_own_sources() -> None:
    rig = _Rig(_FakeLLM())
    deck_id = await rig.deck()
    other_deck = await rig.deck()
    await rig.source(other_deck, _text_source("Mitochondria make ATP."))
    with pytest.raises(NoChatSources):
        await rig.service.ask(owner_id="alice", deck_id=deck_id, question="ATP?")
    assert await rig.passages.indexed_files(other_deck) == set()


async def test_ask_without_a_match_skips_the_model() -> None:
    llm = _FakeLLM()
    rig = _Rig(llm)
    deck_id = await rig.deck()
    await rig.source(deck_id, _text_source("Mitochondria make ATP."))
    answer = await rig.service.ask(owner_id="alice", deck_id=deck_id, question="volcanoes?")
    assert answer.answer == NOT_FOUND_ANSWER and answer.citations == ()
    assert llm.requests == []


async def test_missing_source_blob_is_skipped() -> None:
    rig = _Rig(_FakeLLM())
    deck_id = await rig.deck()
    source = await rig.source(deck_id, _text_source("text"))
    await rig.store.delete(source)
    with pytest.raises(NoChatSources):
        await rig.service.ask(owner_id="alice", deck_id=deck_id, question="text")
    assert await rig.passages.indexed_files(deck_id) == set()


async def test_unreadable_source_is_marked_and_not_retried() -> None:
    rig = _Rig(_FakeLLM())
    deck_id = await rig.deck()
    source = await rig.source(deck_id, b"definitely not a document")
    with pytest.raises(NoChatSources):
        await rig.service.ask(owner_id="alice", deck_id=deck_id, question="anything")
    assert await rig.passages.indexed_files(deck_id) == {source}
    [marker] = await rig.passages.list_for_deck(deck_id)
    assert (marker.page, marker.text) == (0, "")


async def test_image_only_source_has_no_chat_text() -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=32, height=32)
    png = bytes(page.get_pixmap().tobytes("png"))
    doc.close()
    rig = _Rig(_FakeLLM())
    deck_id = await rig.deck()
    await rig.source(deck_id, png)
    with pytest.raises(NoChatSources):
        await rig.service.ask(owner_id="alice", deck_id=deck_id, question="anything")


async def test_ask_is_owner_scoped() -> None:
    rig = _Rig(_FakeLLM())
    deck_id = await rig.deck("alice")
    with pytest.raises(DeckNotFound):
        await rig.service.ask(owner_id="bob", deck_id=deck_id, question="ATP?")


async def test_an_encrypted_pdf_does_not_break_chat_for_the_deck() -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret mitochondria notes")
    encrypted = bytes(
        doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="owner")
    )
    doc.close()
    rig = _Rig(_FakeLLM())
    deck_id = await rig.deck()
    locked = await rig.source(deck_id, encrypted)
    await rig.source(deck_id, _text_source("Mitochondria make ATP."))

    answer = await rig.service.ask(owner_id="alice", deck_id=deck_id, question="ATP?")

    # The readable source still answers; the encrypted one is recorded, not retried.
    assert answer.citations and answer.citations[0].file_id != locked
    assert locked in await rig.passages.indexed_files(deck_id)


async def test_a_source_is_capped_in_passages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("flashcards.application.chat_service.MAX_PASSAGES_PER_SOURCE", 2)
    rig = _Rig(_FakeLLM())
    deck_id = await rig.deck()
    # A long single paste splits into several 1,200-char passages before the cap.
    await rig.source(deck_id, _text_source("ATP " * 2_000))
    await rig.service.ask(owner_id="alice", deck_id=deck_id, question="ATP?")
    assert len(await rig.passages.list_for_deck(deck_id)) == 2


async def test_chat_on_a_parent_deck_uses_its_subdecks_sources() -> None:
    rig = _Rig(_FakeLLM())
    parent = await rig.deck()
    child = (await rig.decks.create(owner_id="alice", name="Child", parent_id=parent)).id
    source = await rig.source(child, _text_source("Mitochondria make ATP."))

    answer = await rig.service.ask(owner_id="alice", deck_id=parent, question="ATP?")

    assert answer.citations == (Citation(source, 1),)
    # Indexed under the deck its job generated into, so deleting that deck cleans it up.
    assert await rig.passages.indexed_files(child) == {source}
    assert await rig.passages.indexed_files(parent) == set()


async def test_in_memory_passages_ignore_duplicate_positions() -> None:
    repo = InMemoryPassageRepository()
    await repo.add_many([_p("first")])
    await repo.add_many([_p("second"), _p("third", ordinal=1)])
    assert [p.text for p in await repo.list_for_deck("d")] == ["first", "third"]
