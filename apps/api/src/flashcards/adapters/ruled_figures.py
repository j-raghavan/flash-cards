# SPDX-License-Identifier: AGPL-3.0-only
"""Ruled figures on a scanned or photographed page: graph grids and frames (ADR 0026).

A vision model names a figure's box only roughly (on a phone-scanned worksheet,
often off by a fifth of the page), so a crop from its box can cut a graph in
half. What a scanned graph has, and text never does, is long straight lines
running both across and down: its grid, axes and frame. The page is rendered
small in grayscale, the dark pixels on long horizontal or vertical runs are
kept, and each connected group of them that has several of each and takes up a
real share of the page is a figure. Two figures joined only by a thin line (one
graph's axis running into the next) are split where little else crosses.

The work is plain loops over about a quarter of a million pixels, a tenth of a
second a page, run on the caller's PyMuPDF worker thread.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from itertools import accumulate

import pymupdf

from flashcards.domain.figures import FIGURE_SCALE, FigureBox

# Pages are measured at this width in pixels (about 60 dots per inch on paper),
# and never at more than _MAX_PIXELS: a page's size comes from the untrusted file,
# and a tall thin one would otherwise measure in the hundreds of millions.
_WIDTH = 500
_MAX_PIXELS = 500_000
# A pixel darker than this is ink; dotted grid lines in a scan are light gray. On
# gray paper (a dim photo) ink is what is this much darker than the paper.
_DARK = 215
_PAPER_CONTRAST = 35
# A run of ink may skip this many light pixels (the gaps in a dotted line).
_GAP = 2
# A line is a run at least this share of the page's width (or height) long.
_MIN_RUN = 0.12
# A figure spans at least this share of the page's width and of its height, with
# at least this many rows of horizontal lines and columns of vertical ones.
_MIN_WIDE = 0.15
_MIN_HIGH = 0.1
_MIN_LINES = 3
# Two figures are split where at most this many line pixels cross a band at
# least _SPLIT_BAND pixels wide (a margin between graphs, not between grid lines).
_THIN = 3
_SPLIT_BAND = 8
# Margin around a measured figure, in thousandths of the page: enough for an axis
# arrow or end label, little enough to leave out answers written beside it.
RULED_MARGIN = 6
# A "figure" over this share of the page is the page (a dark desk around a photo,
# a scanner's shadow), not a figure; cropping it would show the worked answers.
_MAX_AREA = 0.5
# The prompt names at most this many measured figures, as many as output may list.
MAX_MEASURED = 12


@dataclass(frozen=True, slots=True)
class _Box:
    left: int
    top: int
    right: int
    bottom: int


def ruled_figures(page: pymupdf.Page) -> tuple[FigureBox, ...]:
    """The boxes of the ruled figures on a page as shown, padded by RULED_MARGIN."""
    rect = page.rect
    width, height = float(rect.width), float(rect.height)
    zoom = min(_WIDTH / width, math.sqrt(_MAX_PIXELS / (width * height)))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY)
    wide, high = int(pix.width), int(pix.height)
    boxes = [
        _scaled(box, wide, high)
        for box in find_ruled(wide, high, bytes(pix.samples), int(pix.stride))
    ]
    figures = [
        box.padded(RULED_MARGIN)
        for box in boxes
        if box.area <= _MAX_AREA * FIGURE_SCALE * FIGURE_SCALE
    ]
    return tuple(figures[:MAX_MEASURED])


def find_ruled(width: int, height: int, samples: bytes, stride: int) -> list[_Box]:
    """Ruled figures in a grayscale image, in pixels, from the top-left."""
    rows = [samples[y * stride : y * stride + width] for y in range(height)]
    shades = [0] * 256
    for row in rows:
        for value in row:
            shades[value] += 1
    # The paper is the median shade; ink is clearly darker than it.
    half = width * height // 2
    paper = next(shade for shade, seen in enumerate(accumulate(shades)) if seen > half)
    threshold = min(_DARK, paper - _PAPER_CONTRAST)
    dark = bytearray(width * height)
    for y, row in enumerate(rows):
        for x, value in enumerate(row):
            if value < threshold:
                dark[y * width + x] = 1
    across = _runs(width, height, dark, horizontal=True)
    down = _runs(width, height, dark, horizontal=False)
    lines = bytearray(a | b for a, b in zip(across, down, strict=True))
    found: list[_Box] = []
    for pixels in _groups(width, height, lines):
        bounds = _bounds(width, pixels)
        if not _figure_sized(bounds, width, height):
            continue  # a rule or a mark: no piece of it can be a figure
        own = bytearray(width * height)
        for p in pixels:
            own[p] = 1
        # A piece starts and ends on the group's own lines (_split cuts only across
        # bands they barely cross), so it always holds some of its pixels.
        for piece in _split(width, height, own, bounds):
            inside = [
                p
                for p in pixels
                if piece.left <= p % width < piece.right and piece.top <= p // width < piece.bottom
            ]
            box = _bounds(width, inside)
            lines_across = {p // width for p in inside if across[p]}
            columns = {p % width for p in inside if down[p]}
            sized = _figure_sized(box, width, height)
            if sized and len(lines_across) >= _MIN_LINES and len(columns) >= _MIN_LINES:
                found.append(box)
    return found


def _runs(width: int, height: int, dark: bytearray, *, horizontal: bool) -> bytearray:
    """The dark pixels on long straight runs, across or down, allowing small gaps."""
    out = bytearray(width * height)
    lines, length = (height, width) if horizontal else (width, height)
    need = math.ceil(_MIN_RUN * length)

    def at(line: int, offset: int) -> int:
        return line * width + offset if horizontal else offset * width + line

    for line in range(lines):
        start = last = -1
        for offset in range(length + _GAP + 1):
            if offset < length and dark[at(line, offset)]:
                if start < 0:
                    start = offset
                last = offset
            elif start >= 0 and offset - last > _GAP:
                if last - start + 1 >= need:
                    for c in range(start, last + 1):
                        out[at(line, c)] = 1
                start = -1
    return out


def _groups(width: int, height: int, mask: bytearray) -> list[list[int]]:
    """The line pixels of each connected group; pixels up to two apart are joined."""
    grown = bytearray(mask)
    for i, on in enumerate(mask):
        if on:
            y, x = divmod(i, width)
            for yy in range(max(0, y - 1), min(height, y + 2)):
                for xx in range(max(0, x - 1), min(width, x + 2)):
                    grown[yy * width + xx] = 1
    seen = bytearray(width * height)
    groups: list[list[int]] = []
    for start, on in enumerate(grown):
        if not on or seen[start]:
            continue
        seen[start] = 1
        queue = deque([start])
        group: list[int] = []
        while queue:
            i = queue.popleft()
            if mask[i]:
                group.append(i)
            x = i % width
            for j in (
                i - 1 if x > 0 else -1,
                i + 1 if x < width - 1 else -1,
                i - width,
                i + width,
            ):
                if 0 <= j < width * height and grown[j] and not seen[j]:
                    seen[j] = 1
                    queue.append(j)
        groups.append(group)
    return groups


def _split(width: int, height: int, mask: bytearray, box: _Box) -> list[_Box]:
    """A box cut where only a thin line or two crosses a wide band of it.

    Both pieces must be figure-sized, so the gaps between a faint grid's lines
    never cut a graph apart.
    """
    for horizontal in (True, False):
        start, end = (box.left, box.right) if horizontal else (box.top, box.bottom)
        least = _MIN_WIDE * width if horizontal else _MIN_HIGH * height
        profile = [
            sum(
                mask[y * width + a] if horizontal else mask[a * width + y]
                for y in (range(box.top, box.bottom) if horizontal else range(box.left, box.right))
            )
            for a in range(start, end)
        ]
        a = 0
        while a < len(profile):
            if profile[a] > _THIN:
                a += 1
                continue
            b = a
            while b < len(profile) and profile[b] <= _THIN:
                b += 1
            if b - a >= _SPLIT_BAND and a >= least and len(profile) - b >= least:
                if horizontal:
                    first = _Box(box.left, box.top, box.left + a, box.bottom)
                    second = _Box(box.left + b, box.top, box.right, box.bottom)
                else:
                    first = _Box(box.left, box.top, box.right, box.top + a)
                    second = _Box(box.left, box.top + b, box.right, box.bottom)
                return _split(width, height, mask, first) + _split(width, height, mask, second)
            a = b
    return [box]


def _figure_sized(box: _Box, width: int, height: int) -> bool:
    return (box.right - box.left) >= _MIN_WIDE * width and (
        box.bottom - box.top
    ) >= _MIN_HIGH * height


def _bounds(width: int, pixels: list[int]) -> _Box:
    xs = [p % width for p in pixels]
    ys = [p // width for p in pixels]
    return _Box(min(xs), min(ys), max(xs) + 1, max(ys) + 1)


def _scaled(box: _Box, width: int, height: int) -> FigureBox:
    return FigureBox(
        left=box.left * FIGURE_SCALE // width,
        top=box.top * FIGURE_SCALE // height,
        right=math.ceil(box.right * FIGURE_SCALE / width),
        bottom=math.ceil(box.bottom * FIGURE_SCALE / height),
    )
