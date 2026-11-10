# SPDX-License-Identifier: AGPL-3.0-only
"""Extracted source material: a page is either authoritative text or an image.

A DocumentExtractor yields ExtractedPages; generation consumes them. The
SourceRef is the per-card back-reference (F-AI-5).
"""

from __future__ import annotations

import enum
import re
import unicodedata
from dataclasses import dataclass

from flashcards.domain.figures import FigureBox


class PageKind(enum.StrEnum):
    TEXT = "text"  # born-digital: the extracted text is authoritative
    IMAGE = "image"  # scanned/photo/formula/handwriting: hand to the vision model


# Most pages (or transcript chunks) a member's AI source document may have, by
# default (ADR 0022). Admins have no per-document cap. Extraction streams one page
# at a time, so the cap bounds a job's time and cost, not its memory.
DEFAULT_MAX_UPLOAD_PAGES = 300
# Longest source name kept for chat citations, in characters.
MAX_SOURCE_NAME = 120
_SPACES = re.compile(r"\s+")


def page_cap(configured: int, *, admin: bool) -> int | None:
    """The most pages one document may have for this uploader; None means no cap.

    Admins upload documents of any length (ADR 0022); everyone else is held to the
    configured cap (FLASHCARDS_MAX_UPLOAD_PAGES).
    """
    return None if admin else configured


def source_name_key(source_id: str) -> str:
    """Object store key of a source's display name, kept beside the source itself."""
    return f"{source_id}.name"


def clean_source_name(raw: str | None) -> str | None:
    """A source name safe to store and show: no folders, controls or runs of space.

    The name comes from the uploader's browser, so it is only ever display text:
    the folder part is dropped, control and format characters (bidi overrides
    among them) are removed, and it is cut to MAX_SOURCE_NAME characters.
    """
    if raw is None:
        return None
    base = re.split(r"[/\\]", raw)[-1]
    kept = "".join(
        " " if c.isspace() else c for c in base if c.isspace() or unicodedata.category(c)[0] != "C"
    )
    name = _SPACES.sub(" ", kept).strip()[:MAX_SOURCE_NAME].strip()
    return name or None


class UnsupportedMediaType(Exception):
    """The upload is not an allowed type (application/pdf, image/png, image/jpeg)."""


class TooManyPages(Exception):
    """The document has more pages than the configured cap."""

    def __init__(self, pages: int, limit: int, *, transcript: bool = False) -> None:
        # Shown to the uploader as is.
        super().__init__(
            f"This transcript has {pages} parts, each counted as a page; the limit is {limit}."
            if transcript
            else f"This document has {pages} pages; the limit is {limit}."
        )
        self.pages = pages
        self.limit = limit


class UnreadableDocument(Exception):
    """The document is of an allowed type but could not be parsed (corrupt or hostile)."""


@dataclass(frozen=True, slots=True)
class SourceRef:
    file_id: str
    page: int  # 1-based


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    ref: SourceRef
    kind: PageKind
    text: str | None = None
    # The page as an image: always for an IMAGE page, and for a TEXT page that has
    # pictures or drawings, so the model sees them too (F-AI-5).
    image: bytes | None = None
    image_media_type: str | None = None  # image/png or image/jpeg when image is set
    # Exact boxes of the figures found in a PDF page, to snap the model's boxes to.
    figure_boxes: tuple[FigureBox, ...] = ()
