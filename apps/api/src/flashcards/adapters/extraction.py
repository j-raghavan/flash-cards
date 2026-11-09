# SPDX-License-Identifier: AGPL-3.0-only
"""PyMuPDF document extractor (F-AI-1).

Born-digital PDF pages yield their text (authoritative, with an exact page ref);
text-less pages and uploaded images are rasterized/passed through for the vision
model. Content type is sniffed from the bytes, never trusted from the client.
All synchronous PyMuPDF work (opening, counting, text extraction, rendering)
runs in a worker thread under the single-slot PYMUPDF limiter, so a slow or hostile file
never stalls the event loop, and a page is rendered at a resolution that keeps
its pixmap under MAX_RASTER_PIXELS however large the page claims to be.
"""

from __future__ import annotations

import math
from collections.abc import AsyncGenerator, Callable
from functools import partial

import puremagic
import pymupdf

from flashcards.adapters.offload import PYMUPDF, run_blocking
from flashcards.domain.sources import (
    ExtractedPage,
    PageKind,
    SourceRef,
    TooManyPages,
    UnreadableDocument,
    UnsupportedMediaType,
)

_ALLOWED = {"application/pdf", "image/png", "image/jpeg"}
# A page with at least this much extracted text is treated as born-digital text.
_TEXT_THRESHOLD = 16
_RASTER_DPI = 150
_POINTS_PER_INCH = 72
# Upper bound on a rendered page's pixels (about 48 MB as RGB). A letter page at
# 150 DPI is about 2.1 million; larger pages are rendered at a lower resolution.
MAX_RASTER_PIXELS = 16_000_000
# What PyMuPDF raises on a corrupt or hostile file: FileDataError (a RuntimeError)
# when opening, MuPDF's own errors while extracting or rendering.
_PDF_ERRORS = (RuntimeError, pymupdf.mupdf.FzErrorBase)


class PyMuPDFExtractor:
    def sniff(self, data: bytes) -> str:
        try:
            media = str(puremagic.from_string(data, mime=True))
        except puremagic.PureError as exc:
            raise UnsupportedMediaType("could not identify file type") from exc
        if media not in _ALLOWED:
            raise UnsupportedMediaType(media)
        return media

    async def count_pages(self, data: bytes) -> int:
        if self.sniff(data) != "application/pdf":
            return 1
        return await _parse(lambda: _page_count(data))

    async def extract(
        self,
        data: bytes,
        *,
        file_id: str,
        max_pages: int | None,
        skip: int = 0,
        text_only: bool = False,
    ) -> AsyncGenerator[ExtractedPage, None]:
        media = self.sniff(data)
        if media != "application/pdf":
            if skip > 0 or text_only:
                return
            yield ExtractedPage(
                ref=SourceRef(file_id=file_id, page=1),
                kind=PageKind.IMAGE,
                image=data,
                image_media_type=media,
            )
            return

        doc, page_count = await _parse(lambda: _open(data))
        try:
            if max_pages is not None and page_count > max_pages:
                raise TooManyPages(page_count, max_pages)
            # One page is rendered at a time and handed on before the next, so peak
            # memory is about one page whatever the page count (ADR 0022); pages
            # before `skip` are not rendered at all.
            for index in range(max(skip, 0), page_count):
                page = await _parse(partial(_render_page, doc, index, file_id, text_only))
                if page is not None:
                    yield page
        finally:
            # Closing touches MuPDF too, so it takes the same single slot.
            await run_blocking(PYMUPDF, doc.close)


async def _parse[T](work: Callable[[], T]) -> T:
    """Run PyMuPDF work in a worker thread; its failures become UnreadableDocument.

    PyMuPDF does not support concurrent use from several threads, so all of it
    (request-time counting, worker rendering, chat indexing) shares one slot.
    """
    try:
        return await run_blocking(PYMUPDF, work)
    except _PDF_ERRORS as exc:
        raise UnreadableDocument(type(exc).__name__) from exc


def _open(data: bytes) -> tuple[pymupdf.Document, int]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    return doc, int(doc.page_count)


def _page_count(data: bytes) -> int:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return int(doc.page_count)
    finally:
        doc.close()


def raster_zoom(width: float, height: float) -> float:
    """The scale to render a page of this size (in points) at, within MAX_RASTER_PIXELS.

    The page's dimensions come from the untrusted file and are checked before
    anything is allocated: a page that would exceed the cap at _RASTER_DPI is
    rendered at the largest resolution that fits instead.
    """
    zoom = _RASTER_DPI / _POINTS_PER_INCH
    area = width * height
    if area * zoom * zoom > MAX_RASTER_PIXELS:
        zoom = math.sqrt(MAX_RASTER_PIXELS / area)
    return zoom


def _render_page(
    doc: pymupdf.Document, index: int, file_id: str, text_only: bool = False
) -> ExtractedPage | None:
    page = doc[index]
    text = str(page.get_text()).strip()
    ref = SourceRef(file_id=file_id, page=index + 1)
    if len(text) >= _TEXT_THRESHOLD:
        return ExtractedPage(ref=ref, kind=PageKind.TEXT, text=text)
    if text_only:
        return None  # a text-less page is not rasterized when only text is wanted
    rect = page.rect
    zoom = raster_zoom(float(rect.width), float(rect.height))
    png = bytes(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png"))
    return ExtractedPage(ref=ref, kind=PageKind.IMAGE, image=png, image_media_type="image/png")
