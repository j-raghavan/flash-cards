# SPDX-License-Identifier: AGPL-3.0-only
"""Figures on a source page that a generated card shows with it (F-AI-5).

A card about a graph or diagram means nothing without the picture, so the model
names the figure's box on the page it was shown, the figure is cropped from that
page image, stored as the owner's media, and embedded in the card's text as a
``media:`` image the client resolves on its own origin. Boxes are integers in
thousandths of the page's width and height, measured from its top-left corner,
so they do not depend on the resolution the page was rendered at.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from flashcards.domain.notes import Fields, NoteType

FIGURE_SCALE = 1000
# Most figures one page's output may name; more is not a page of study material.
MAX_FIGURES = 12
# Margin added on every side of a model's own box, in thousandths: a model's box
# is approximate, and cutting off an axis label costs more than a little extra page.
FIGURE_MARGIN = 40
# A model's box names a figure measured on the page itself when they overlap at
# least this much (intersection over union); the measured box is what is cropped.
SNAP_THRESHOLD = 0.5
# A model's own box is used only when it covers at most this share of the page: a
# box over most of the page is the page, with its worked answers.
MAX_GUESSED_AREA = 0.5


class InvalidFigure(ValueError):
    """A figure box that is not a non-empty rectangle within the page."""


@dataclass(frozen=True, slots=True)
class FigureBox:
    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        if not (
            0 <= self.left < self.right <= FIGURE_SCALE
            and 0 <= self.top < self.bottom <= FIGURE_SCALE
        ):
            raise InvalidFigure("a figure box must be a non-empty rectangle within the page")

    @property
    def area(self) -> int:
        return (self.right - self.left) * (self.bottom - self.top)

    def padded(self, margin: int = FIGURE_MARGIN) -> FigureBox:
        return FigureBox(
            left=max(0, self.left - margin),
            top=max(0, self.top - margin),
            right=min(FIGURE_SCALE, self.right + margin),
            bottom=min(FIGURE_SCALE, self.bottom + margin),
        )

    def overlap(self, other: FigureBox) -> float:
        """Intersection over union, from 0 (apart) to 1 (the same box)."""
        width = min(self.right, other.right) - max(self.left, other.left)
        height = min(self.bottom, other.bottom) - max(self.top, other.top)
        if width <= 0 or height <= 0:
            return 0.0
        shared = width * height
        return shared / (self.area + other.area - shared)


def snap(box: FigureBox, found: tuple[FigureBox, ...], *, guess: bool) -> FigureBox | None:
    """The box to crop for a model's figure box, or None when there is none.

    A figure measured on the page (an embedded image, a cluster of drawings or a
    ruled graph in a scan) has an exact box that already carries the margin its
    labels need, so the best match is cropped as it is. Otherwise the model's own
    box is cropped with the full margin only where guessing is allowed (a
    born-digital page) and it covers at most half the page. On a scan the model's
    box misses by up to a fifth of the page, cutting figures in half or taking in
    the labels and worked answers around them, so it is never cropped (ADR 0026).
    """
    best = max(found, key=box.overlap, default=None)
    if best is not None and box.overlap(best) >= SNAP_THRESHOLD:
        return best
    if not guess or box.area > MAX_GUESSED_AREA * FIGURE_SCALE * FIGURE_SCALE:
        return None
    return box.padded()


def figure_markdown(media_id: str) -> str:
    """The image a card's text carries; the client maps ``media:`` to its media URL."""
    return f"![Figure](media:{media_id})"


def with_figure(note_type: NoteType, fields: Fields, media_id: str) -> Fields:
    """The fields with the figure shown first on the card's question side.

    A Basic card shows it above its front, a Cloze card above its text, so the
    figure is in view while the question is asked.
    """
    target = "front" if note_type is NoteType.BASIC else "text"
    image = figure_markdown(media_id)
    return tuple(
        (name, f"{image}\n\n{value}" if name == target else value) for name, value in fields
    )


# A `media:` image in card text, as figure_markdown writes it (or a user types it).
_MEDIA_IMAGE = re.compile(r"(!\[[^\]\n]*\]\(media:)([A-Za-z0-9-]{1,64})\)")


def media_ids(fields: Fields) -> list[str]:
    """The media ids the fields' `media:` images name, in order, each once."""
    found = (match.group(2) for _, value in fields for match in _MEDIA_IMAGE.finditer(value))
    return list(dict.fromkeys(found))


def with_media_ids(fields: Fields, copies: Mapping[str, str]) -> Fields:
    """The fields with each `media:` image pointing at its copy, where it has one."""

    def swap(match: re.Match[str]) -> str:
        return f"{match.group(1)}{copies.get(match.group(2), match.group(2))})"

    return tuple((name, _MEDIA_IMAGE.sub(swap, value)) for name, value in fields)
