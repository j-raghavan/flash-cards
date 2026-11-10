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
from collections.abc import AsyncGenerator, Callable, Mapping
from functools import partial

import puremagic
import pymupdf

from flashcards.adapters.offload import PYMUPDF, run_blocking
from flashcards.domain.figures import FIGURE_SCALE, FigureBox
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
# A picture or drawing counts as a figure when each side spans at least this
# share of the page and it covers at least _MIN_GRAPHIC_AREA of it; one covering
# _BACKGROUND_AREA or more is the page itself (a scan under a text layer).
_MIN_GRAPHIC_SIDE = 0.05
_MIN_GRAPHIC_AREA = 0.02
_BACKGROUND_AREA = 0.9
# Drawings whose box is at least this much covered by words are a frame or a
# table around text; a graph's axis labels cover far less (measured: CED frames
# and tables 0.075 and up, graphs under 0.03).
_MAX_FIGURE_TEXT_INK = 0.05
# A page-covering image at least this sharp is a scan of the page, which the
# model must see; a blurrier one is a slide background (measured: about 40).
_MIN_SCAN_DPI = 100
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
        if text_only:
            return ExtractedPage(ref=ref, kind=PageKind.TEXT, text=text)
        has_graphics, figures = graphic_boxes(page)
        if not has_graphics:
            return ExtractedPage(ref=ref, kind=PageKind.TEXT, text=text)
        # A page with a picture or drawing is also sent as an image, so the model
        # sees the graph its text only labels, and can name its box (F-AI-5).
        return ExtractedPage(
            ref=ref,
            kind=PageKind.TEXT,
            text=text,
            image=_raster(page),
            image_media_type="image/png",
            figure_boxes=figures,
        )
    if text_only:
        return None  # a text-less page is not rasterized when only text is wanted
    return ExtractedPage(
        ref=ref, kind=PageKind.IMAGE, image=_raster(page), image_media_type="image/png"
    )


def _raster(page: pymupdf.Page) -> bytes:
    rect = page.rect
    zoom = raster_zoom(float(rect.width), float(rect.height))
    return bytes(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png"))


def graphic_boxes(page: pymupdf.Page) -> tuple[bool, tuple[FigureBox, ...]]:
    """Whether a page has pictures or drawings, and the boxes of those that are figures.

    Embedded images and clusters of vector drawings count when they take up a
    real share of the page; rules, underlines, small marks and the lines of a
    frame or table around text do not. A drawing covering nearly the whole page
    is a background fill and is left out before clustering, so it neither counts
    nor swallows the figures drawn on it. An image covering nearly the whole page
    (a scan under its text layer) means the page has graphics, but it is the
    page, not a figure to crop. Boxes are in the page as shown, after rotation.
    """
    shown = page.rect
    page_area = shown.get_area()

    def placed(rect: pymupdf.Rect) -> pymupdf.Rect:
        # Image and drawing boxes are in unrotated page space; the raster is not.
        return (pymupdf.Rect(rect) * page.rotation_matrix) & shown

    covers = page_area * _BACKGROUND_AREA
    drawings = [d for d in page.get_drawings() if placed(d["rect"]).get_area() < covers]
    words = [placed(pymupdf.Rect(word[:4])) for word in page.get_text("words")]
    candidates = [
        (placed(pymupdf.Rect(info["bbox"])), _scan_resolution(info, shown))
        for info in page.get_image_info()
    ]
    for cluster in page.cluster_drawings(drawings=drawings) if drawings else []:
        drawn = placed(cluster)
        # Lines around text are a frame or a table, not a figure: a graph or a
        # diagram carries only sparse labels.
        ink = sum(word.get_area() for word in words if word.intersects(drawn))
        if not drawn.is_empty and ink / drawn.get_area() < _MAX_FIGURE_TEXT_INK:
            candidates.append((drawn, False))
    has_graphics = False
    figures: list[FigureBox] = []
    for rect, scan_like in candidates:
        if rect.is_empty:
            continue
        w, h = rect.width / shown.width, rect.height / shown.height
        if w < _MIN_GRAPHIC_SIDE or h < _MIN_GRAPHIC_SIDE or w * h < _MIN_GRAPHIC_AREA:
            continue
        if w * h >= _BACKGROUND_AREA:
            has_graphics = has_graphics or scan_like  # a scanned page, not a figure
            continue
        has_graphics = True
        figures.append(
            FigureBox(
                left=math.floor((rect.x0 - shown.x0) / shown.width * FIGURE_SCALE),
                top=math.floor((rect.y0 - shown.y0) / shown.height * FIGURE_SCALE),
                right=math.ceil((rect.x1 - shown.x0) / shown.width * FIGURE_SCALE),
                bottom=math.ceil((rect.y1 - shown.y0) / shown.height * FIGURE_SCALE),
            )
        )
    return has_graphics, tuple(figures)


def _scan_resolution(info: Mapping[str, object], shown: pymupdf.Rect) -> bool:
    """Whether a page-covering image is sharp enough to be a scan of the page.

    A slide's background picture is stretched over the page at a few dozen dots
    per inch; a scan or photo of a page is 150 or more.
    """
    width = info.get("width")
    return isinstance(width, int) and width / (shown.width / _POINTS_PER_INCH) >= _MIN_SCAN_DPI


class PyMuPDFFigureCropper:
    """Crops a figure out of a rendered page image (domain.figures), as a PNG."""

    async def crop(self, image: bytes, box: FigureBox) -> bytes:
        return await _parse(partial(_crop, image, box))


def _crop(image: bytes, box: FigureBox) -> bytes:
    # Only the page images this app renders or accepts (PNG, JPEG) are cropped, and
    # opened as exactly that type, so a polyglot file is never read as another kind.
    if image.startswith(b"\x89PNG"):
        filetype = "png"
    elif image.startswith(b"\xff\xd8"):
        filetype = "jpeg"
    else:
        raise UnreadableDocument("not a PNG or JPEG page image")
    doc = pymupdf.open(stream=image, filetype=filetype)
    try:
        page = doc[0]
        infos = page.get_image_info()
        # Read from the image header before anything is decoded: MuPDF decodes the
        # whole image to render any part of it, so a small file claiming a huge
        # size is refused here rather than allocated.
        if not infos:  # pragma: no cover - a PNG or JPEG that opens is one image
            raise UnreadableDocument("no image to crop")
        pixels_wide, pixels_high = int(infos[0]["width"]), int(infos[0]["height"])
        if pixels_wide * pixels_high > MAX_RASTER_PIXELS:
            raise UnreadableDocument("the page image is too large to crop")
        r = page.rect
        clip = pymupdf.Rect(
            r.x0 + r.width * box.left / FIGURE_SCALE,
            r.y0 + r.height * box.top / FIGURE_SCALE,
            r.x0 + r.width * box.right / FIGURE_SCALE,
            r.y0 + r.height * box.bottom / FIGURE_SCALE,
        )
        # Render at the image's own resolution (an image opens at its DPI's size in
        # points), which the check above keeps within MAX_RASTER_PIXELS.
        zoom = pixels_wide / float(r.width)
        return bytes(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip).tobytes("png"))
    finally:
        doc.close()
