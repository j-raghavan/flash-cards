# SPDX-License-Identifier: AGPL-3.0-only
"""Chat with a deck's source documents, with file and page citations (F-AI-8).

A deck's sources (the documents and transcripts its AI jobs were generated from)
are split into passages and indexed. A question retrieves the best passages with
BM25, the model answers from those passages only, and cites them by number; the
domain maps each cited number back to its file and page. Retrieval is lexical so
it works with any backend and any Postgres (ADR 0008).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from flashcards.domain.ai import InvalidModelOutput

MAX_QUESTION_LEN = 2_000
PASSAGE_CHARS = 1_200
# About 2.4 MB of text: plenty for 20 dense pages, bounded against inflated PDFs.
MAX_PASSAGES_PER_SOURCE = 2_000
TOP_K = 6
CHAT_MAX_OUTPUT_TOKENS = 1_500
NOT_FOUND_ANSWER = "I could not find this in the deck's source documents."
# BM25 parameters (the usual defaults).
_K1 = 1.5
_B = 0.75
_TOKEN = re.compile(r"\w+")
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
        "in", "is", "it", "its", "of", "on", "or", "that", "the", "this", "to", "was",
        "were", "what", "when", "where", "which", "who", "why", "how", "with", "does",
        "do", "did",
    }
)


class NoChatSources(Exception):
    """The deck has no source documents with readable text to chat with."""


class InvalidChatAnswer(InvalidModelOutput):
    """The model's answer did not match the chat schema."""


@dataclass(frozen=True, slots=True)
class Passage:
    id: str
    owner_id: str
    deck_id: str
    file_id: str
    page: int  # 1-based source page; 0 marks a file indexed with no readable text
    ordinal: int  # position within the page
    text: str


@dataclass(frozen=True, slots=True)
class Citation:
    file_id: str
    page: int
    file_name: str | None = None  # the uploaded file's name, when it was kept


@dataclass(frozen=True, slots=True)
class ChatAnswer:
    answer: str
    citations: tuple[Citation, ...]


CHAT_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["answer", "citations"],
}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.casefold()) if t not in _STOPWORDS]


def split_passages(text: str, max_chars: int = PASSAGE_CHARS) -> list[str]:
    """Split page text into passages of at most max_chars, keeping paragraphs together."""
    passages: list[str] = []
    current = ""
    for paragraph in (p.strip() for p in re.split(r"\n\s*\n", text)):
        for piece in (paragraph[i : i + max_chars] for i in range(0, len(paragraph), max_chars)):
            if current and len(current) + 1 + len(piece) > max_chars:
                passages.append(current)
                current = ""
            current = f"{current} {piece}" if current else piece
    if current:
        passages.append(current)
    return passages


def rank_passages(passages: Sequence[Passage], query: str, k: int = TOP_K) -> list[Passage]:
    """Return up to k passages ranked by BM25 against the query; no overlap means none."""
    terms = set(tokenize(query))
    docs = [(p, tokenize(p.text)) for p in passages if p.text.strip()]
    if not terms or not docs:
        return []
    count = len(docs)
    avg_len = (sum(len(tokens) for _, tokens in docs) / count) or 1.0
    doc_freq: Counter[str] = Counter()
    for _, tokens in docs:
        doc_freq.update(set(tokens))
    scored: list[tuple[float, int, Passage]] = []
    for position, (passage, tokens) in enumerate(docs):
        freqs = Counter(tokens)
        score = 0.0
        for term in terms:
            freq = freqs.get(term, 0)
            if not freq:
                continue
            idf = math.log(1 + (count - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            norm = freq + _K1 * (1 - _B + _B * len(tokens) / avg_len)
            score += idf * freq * (_K1 + 1) / norm
        if score > 0:
            scored.append((score, position, passage))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [passage for _, _, passage in scored[:k]]


def numbered_context(passages: Sequence[Passage]) -> str:
    """The retrieved passages as one data document, numbered for citation."""
    return "\n\n".join(
        f"[{n}] (source {p.file_id}, page {p.page})\n{p.text}"
        for n, p in enumerate(passages, start=1)
    )


def parse_chat_answer(raw: Mapping[str, object], passages: Sequence[Passage]) -> ChatAnswer:
    answer = raw.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise InvalidChatAnswer("the answer is missing")
    numbers = raw.get("citations")
    if not isinstance(numbers, list):
        raise InvalidChatAnswer("citations are missing or not a list")
    citations: list[Citation] = []
    for number in numbers:
        # Ignore anything that does not point at a passage we actually sent.
        if isinstance(number, bool) or not isinstance(number, int):
            continue
        if not 1 <= number <= len(passages):
            continue
        passage = passages[number - 1]
        citation = Citation(file_id=passage.file_id, page=passage.page)
        if citation not in citations:
            citations.append(citation)
    return ChatAnswer(answer=answer.strip(), citations=tuple(citations))
