# SPDX-License-Identifier: AGPL-3.0-only
"""Ruled figures on scanned pages: graph grids found in the page image (ADR 0026)."""

from __future__ import annotations

import pymupdf
import pytest

from flashcards.adapters import extraction
from flashcards.adapters.extraction import PyMuPDFExtractor
from flashcards.adapters.ruled_figures import MAX_MEASURED, RULED_MARGIN, ruled_figures
from flashcards.domain.figures import FigureBox
from flashcards.domain.sources import ExtractedPage, PageKind

_TEXT = "Section 11.7 Systems of inequalities, worked examples for the chapter"


def _grid(page: pymupdf.Page, rect: pymupdf.Rect, *, dashes: str | None = None) -> None:
    """A coordinate grid of ten squares a side, like a worksheet's graph."""
    step = rect.width / 10
    for i in range(11):
        x, y = rect.x0 + i * step, rect.y0 + i * rect.height / 10
        page.draw_line((x, rect.y0), (x, rect.y1), dashes=dashes)
        page.draw_line((rect.x0, y), (rect.x1, y), dashes=dashes)


def _thousandths(page: pymupdf.Page, rect: pymupdf.Rect) -> FigureBox:
    r = page.rect
    return FigureBox(
        round(rect.x0 / r.width * 1000),
        round(rect.y0 / r.height * 1000),
        round(rect.x1 / r.width * 1000),
        round(rect.y1 / r.height * 1000),
    )


def _close(found: FigureBox, drawn: FigureBox) -> bool:
    """The found box is the drawn one, padded by the ruled margin (to a pixel or two)."""
    want = drawn.padded(RULED_MARGIN)
    return all(
        abs(a - b) <= 4
        for a, b in zip(
            (found.left, found.top, found.right, found.bottom),
            (want.left, want.top, want.right, want.bottom),
            strict=True,
        )
    )


def test_a_graph_grid_is_a_figure_with_its_box() -> None:
    page = pymupdf.open().new_page()
    page.insert_text((72, 72), _TEXT)
    rect = pymupdf.Rect(300, 200, 500, 400)
    _grid(page, rect, dashes="[1 1] 0")  # a dotted grid, as printed worksheets have
    # The x-axis arrow runs a little past the grid: a thin tail, not a second figure.
    page.draw_line((300, 300), (530, 300))
    (box,) = ruled_figures(page)
    assert _close(box, _thousandths(page, pymupdf.Rect(300, 200, 530, 400)))


def test_two_graphs_joined_by_an_axis_are_two_figures() -> None:
    page = pymupdf.open().new_page()
    left, right = pymupdf.Rect(40, 400, 280, 640), pymupdf.Rect(320, 400, 560, 640)
    _grid(page, left)
    _grid(page, right)
    page.draw_line((280, 520), (320, 520))  # one graph's axis runs into the next
    found = sorted(ruled_figures(page), key=lambda box: box.left)
    assert len(found) == 2
    assert _close(found[0], _thousandths(page, left))
    assert _close(found[1], _thousandths(page, right))


def test_two_graphs_one_above_the_other_are_two_figures() -> None:
    page = pymupdf.open().new_page()
    upper, lower = pymupdf.Rect(300, 100, 500, 300), pymupdf.Rect(300, 400, 500, 600)
    _grid(page, upper)
    _grid(page, lower)
    page.draw_line((400, 300), (400, 400))  # the upper graph's y-axis runs down into the lower
    found = sorted(ruled_figures(page), key=lambda box: box.top)
    assert len(found) == 2
    assert _close(found[0], _thousandths(page, upper))
    assert _close(found[1], _thousandths(page, lower))


def test_text_rules_small_grids_and_narrow_frames_are_not_figures() -> None:
    page = pymupdf.open().new_page()
    page.insert_textbox(pymupdf.Rect(72, 72, 520, 400), "Solve each system. " * 60)
    page.draw_line((72, 450), (520, 450))  # a rule across the page
    _grid(page, pymupdf.Rect(72, 500, 132, 560))  # a grid too small to study
    page.draw_rect(pymupdf.Rect(300, 600, 340, 800))  # a narrow frame
    page.draw_line((380, 600), (580, 600))  # bare axes: large, but two lines are no grid
    page.draw_line((480, 500), (480, 700))
    assert ruled_figures(page) == ()


def test_a_faint_grid_is_not_cut_apart() -> None:
    # Only a few vertical lines survive, so bands of rows are crossed by fewer than
    # the split threshold; the pieces either side would be too small to be figures.
    page = pymupdf.open().new_page()
    rect = pymupdf.Rect(300, 200, 500, 400)
    for i in range(11):
        y = rect.y0 + i * 20
        page.draw_line((rect.x0, y), (rect.x1, y))
    for x in (300, 400, 500):
        page.draw_line((x, 200), (x, 400))
    (box,) = ruled_figures(page)
    assert _close(box, _thousandths(page, rect))


def test_a_grid_over_most_of_the_page_is_the_page_not_a_figure() -> None:
    page = pymupdf.open().new_page()
    _grid(page, pymupdf.Rect(20, 20, 575, 820))
    assert ruled_figures(page) == ()


def test_ink_is_judged_against_the_paper() -> None:
    # A dim photo: gray paper with a darker grid. Its grid is found...
    page = pymupdf.open().new_page()
    page.draw_rect(page.rect, color=None, fill=(0.7, 0.7, 0.7))
    rect = pymupdf.Rect(300, 200, 500, 400)
    for i in range(11):
        x, y = rect.x0 + i * 20, rect.y0 + i * 20
        page.draw_line((x, rect.y0), (x, rect.y1), color=(0.2, 0.2, 0.2))
        page.draw_line((rect.x0, y), (rect.x1, y), color=(0.2, 0.2, 0.2))
    (box,) = ruled_figures(page)
    assert _close(box, _thousandths(page, rect))
    # ...and an all-dark page has no ink to find.
    dark = pymupdf.open().new_page()
    dark.draw_rect(dark.rect, color=None, fill=(0, 0, 0))
    assert ruled_figures(dark) == ()


def test_a_tall_thin_page_is_measured_small() -> None:
    # 500 pixels wide would be 360 million pixels; the measure stays bounded.
    page = pymupdf.open().new_page(width=20, height=14_400)
    page.draw_rect(page.rect, color=None, fill=(0, 0, 0))
    pix_before = page.get_pixmap(matrix=pymupdf.Matrix(0.1, 0.1))  # the page renders at all
    assert pix_before.width >= 1 and ruled_figures(page) == ()


def test_at_most_twelve_measured_figures_are_named() -> None:
    page = pymupdf.open().new_page()
    for row in range(5):
        for column in range(3):
            x, y = 30 + column * 190, 30 + row * 160
            _grid(page, pymupdf.Rect(x, y, x + 140, y + 110))
    assert len(ruled_figures(page)) == MAX_MEASURED


def _grid_pdf(*, text: bool) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), _TEXT)
    _grid(page, pymupdf.Rect(300, 200, 500, 400))
    data: bytes = doc.tobytes()
    doc.close()
    return data


def _scan(pdf: bytes) -> bytes:
    """The page as a scanner sees it: a 150 dpi PNG."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    data: bytes = doc[0].get_pixmap(dpi=150).tobytes("png")
    doc.close()
    return data


async def _pages(data: bytes) -> list[ExtractedPage]:
    return [p async for p in PyMuPDFExtractor().extract(data, file_id="f", max_pages=5)]


async def test_a_scanned_page_and_an_uploaded_photo_carry_their_ruled_figures() -> None:
    (pdf_page,) = await _pages(_grid_pdf(text=False))  # a page with no text: an image page
    (upload,) = await _pages(_scan(_grid_pdf(text=False)))
    for page in (pdf_page, upload):
        assert page.kind is PageKind.IMAGE and page.measured_only
        (box,) = page.figure_boxes
        assert abs(box.left - 504) <= 12 and abs(box.bottom - 475) <= 12


async def test_a_scan_under_a_text_layer_carries_its_ruled_figures() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=_scan(_grid_pdf(text=False)), keep_proportion=False)
    page.insert_text((72, 72), _TEXT)
    (extracted,) = await _pages(doc.tobytes())
    assert extracted.kind is PageKind.TEXT and extracted.image is not None
    assert extracted.measured_only
    (box,) = extracted.figure_boxes
    assert abs(box.left - 504) <= 12


async def test_a_page_that_cannot_be_measured_keeps_its_image_without_figures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fails(page: pymupdf.Page) -> tuple[FigureBox, ...]:
        raise RuntimeError("cannot render")

    monkeypatch.setattr(extraction, "ruled_figures", fails)
    (page,) = await _pages(_grid_pdf(text=False))
    assert page.kind is PageKind.IMAGE and page.image is not None and page.figure_boxes == ()


async def test_an_upload_too_large_to_measure_is_still_sent_without_figures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extraction, "MAX_RASTER_PIXELS", 1000)
    (page,) = await _pages(_scan(_grid_pdf(text=False)))
    assert page.kind is PageKind.IMAGE and page.image is not None and page.figure_boxes == ()
