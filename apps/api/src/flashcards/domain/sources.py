# SPDX-License-Identifier: AGPL-3.0-only
"""Extracted source material: a page is either authoritative text or an image.

A DocumentExtractor yields ExtractedPages; generation consumes them. The
SourceRef is the per-card back-reference (F-AI-5).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class PageKind(enum.StrEnum):
    TEXT = "text"  # born-digital: the extracted text is authoritative
    IMAGE = "image"  # scanned/photo/formula/handwriting: hand to the vision model


class UnsupportedMediaType(Exception):
    """The upload is not an allowed type (application/pdf, image/png, image/jpeg)."""


class TooManyPages(Exception):
    """The document has more pages than the configured cap."""


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
    image: bytes | None = None
    image_media_type: str | None = None  # image/png or image/jpeg when kind is IMAGE
