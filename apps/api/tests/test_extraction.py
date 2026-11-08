# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pymupdf
import pytest

from flashcards.adapters.extraction import PyMuPDFExtractor
from flashcards.domain.sources import ExtractedPage, PageKind, TooManyPages, UnsupportedMediaType

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
