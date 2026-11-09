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


class PageKind(enum.StrEnum):
    TEXT = "text"  # born-digital: the extracted text is authoritative
    IMAGE = "image"  # scanned/photo/formula/handwriting: hand to the vision model


# Most pages (or transcript chunks) an AI source document may have, by default.
DEFAULT_MAX_UPLOAD_PAGES = 20
# Longest source name kept for chat citations, in characters.
MAX_SOURCE_NAME = 120
_SPACES = re.compile(r"\s+")


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
