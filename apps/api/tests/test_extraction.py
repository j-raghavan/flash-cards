# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pymupdf
import pytest

from flashcards.adapters import extraction
from flashcards.adapters.extraction import MAX_RASTER_PIXELS, PyMuPDFExtractor, raster_zoom
from flashcards.domain.sources import (
    ExtractedPage,
    PageKind,
    TooManyPages,
    UnreadableDocument,
    UnsupportedMediaType,
)

# A valid 1x1 GIF: a real media type that is not on the allow-list.
_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def _pdf(pages: list[str]) -> bytes:
    doc = pymupdf.open()
    for content in pages:
        page = doc.new_page()
        if content:
            page.insert_text((72, 72), content)
    data: bytes = doc.tobytes()
    doc.close()
    return data


def _png() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    png: bytes = page.get_pixmap().tobytes("png")
    doc.close()
    return png


async def _pages(data: bytes, *, max_pages: int = 20) -> list[ExtractedPage]:
    extractor = PyMuPDFExtractor()
    return [p async for p in extractor.extract(data, file_id="file-1", max_pages=max_pages)]


def test_sniff_allows_pdf_and_rejects_unknown_and_disallowed() -> None:
    extractor = PyMuPDFExtractor()
    assert extractor.sniff(_pdf(["hello world text here"])) == "application/pdf"
    with pytest.raises(UnsupportedMediaType):
        extractor.sniff(b"this is not any known file type at all")
    with pytest.raises(UnsupportedMediaType):
        extractor.sniff(_GIF)


async def test_text_pdf_yields_text_page() -> None:
    pages = await _pages(_pdf(["Hello world this is a born-digital test page"]))
    assert len(pages) == 1
    assert pages[0].kind is PageKind.TEXT
    assert "born-digital" in (pages[0].text or "")
    assert pages[0].ref.page == 1
    assert pages[0].ref.file_id == "file-1"


async def test_textless_pdf_page_is_rasterized() -> None:
    pages = await _pages(_pdf([""]))
    assert len(pages) == 1
    assert pages[0].kind is PageKind.IMAGE
    assert pages[0].image is not None and pages[0].image[:8] == b"\x89PNG\r\n\x1a\n"
    assert pages[0].image_media_type == "image/png"


async def test_multipage_pdf_yields_each_page() -> None:
    pages = await _pages(_pdf(["First page has plenty of text", "Second page also has text"]))
    assert [p.ref.page for p in pages] == [1, 2]


async def test_image_upload_is_a_single_image_page() -> None:
    png = _png()
    pages = await _pages(png)
    assert len(pages) == 1
    assert pages[0].kind is PageKind.IMAGE
    assert pages[0].image == png
    assert pages[0].image_media_type == "image/png"


async def test_too_many_pages_is_rejected() -> None:
    with pytest.raises(TooManyPages):
        await _pages(_pdf(["one page of text here", "second page of text"]), max_pages=1)


async def test_count_pages() -> None:
    extractor = PyMuPDFExtractor()
    assert await extractor.count_pages(_pdf(["a", "b", "c"])) == 3
    assert await extractor.count_pages(_png()) == 1


def test_raster_zoom_keeps_normal_pages_at_150_dpi() -> None:
    assert raster_zoom(612, 792) == pytest.approx(150 / 72)


def test_raster_zoom_shrinks_huge_pages_under_the_pixel_cap() -> None:
    width, height = 14_400.0, 14_400.0  # the largest page PDF allows without UserUnit
    zoom = raster_zoom(width, height)
    assert zoom < 150 / 72
    assert (width * zoom) * (height * zoom) <= MAX_RASTER_PIXELS * 1.0001


def test_raster_zoom_handles_an_empty_page() -> None:
    assert raster_zoom(0, 0) == pytest.approx(150 / 72)


async def test_a_huge_textless_page_renders_within_the_pixel_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A small cap keeps the test fast; at 150 DPI the page would be 9 million pixels.
    monkeypatch.setattr(extraction, "MAX_RASTER_PIXELS", 40_000)
    doc = pymupdf.open()
    doc.new_page(width=1440, height=1440)
    data: bytes = doc.tobytes()
    doc.close()
    pages = await _pages(data)
    image = pymupdf.Pixmap(pages[0].image or b"")
    assert image.width * image.height <= 40_000 + image.width + image.height + 1


_CORRUPT_PDF = b"%PDF-1.4\n" + b"\x00garbage" * 64


async def test_a_corrupt_pdf_is_unreadable_when_counting() -> None:
    with pytest.raises(UnreadableDocument):
        await PyMuPDFExtractor().count_pages(_CORRUPT_PDF)


async def test_a_corrupt_pdf_is_unreadable_when_extracting() -> None:
    with pytest.raises(UnreadableDocument):
        await _pages(_CORRUPT_PDF)


async def test_skip_leaves_out_earlier_pages_without_rendering_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered: list[int] = []
    real = extraction._render_page

    def counting(
        doc: pymupdf.Document, index: int, file_id: str, text_only: bool = False
    ) -> ExtractedPage | None:
        rendered.append(index)
        return real(doc, index, file_id, text_only)

    monkeypatch.setattr(extraction, "_render_page", counting)
    data = _pdf(["First page has plenty of text", "", "Third page also has text"])
    extractor = PyMuPDFExtractor()
    pages = [p async for p in extractor.extract(data, file_id="f", max_pages=None, skip=1)]
    assert [p.ref.page for p in pages] == [2, 3]
    assert rendered == [1, 2]


async def test_no_cap_allows_any_page_count() -> None:
    data = _pdf([f"Page {i} has plenty of text" for i in range(5)])
    extractor = PyMuPDFExtractor()
    pages = [p async for p in extractor.extract(data, file_id="f", max_pages=None)]
    assert len(pages) == 5


async def test_the_cap_error_states_the_count_and_the_limit() -> None:
    with pytest.raises(TooManyPages) as caught:
        await _pages(_pdf(["one page of text here", "second page of text"]), max_pages=1)
    assert str(caught.value) == "This document has 2 pages; the limit is 1."
    assert (caught.value.pages, caught.value.limit) == (2, 1)


async def test_text_only_leaves_out_textless_pages_and_images() -> None:
    extractor = PyMuPDFExtractor()
    data = _pdf(["First page has plenty of text", ""])
    pages = [p async for p in extractor.extract(data, file_id="f", max_pages=None, text_only=True)]
    assert [(p.ref.page, p.kind) for p in pages] == [(1, PageKind.TEXT)]
    images = extractor.extract(_png(), file_id="f", max_pages=None, text_only=True)
    assert [p async for p in images] == []
    skipped = extractor.extract(_png(), file_id="f", max_pages=None, skip=1)
    assert [p async for p in skipped] == []
