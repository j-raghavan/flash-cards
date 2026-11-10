# SPDX-License-Identifier: AGPL-3.0-only
"""Figures a generated card shows: boxes, parsing, detection, cropping and storage."""

from __future__ import annotations

import pymupdf
import pytest
from test_generation import (
    ALICE,
    FakeClock,
    FakeExtractor,
    RecordingUnitOfWork,
    _seed_job,
    _spend,
)

from flashcards.adapters import extraction
from flashcards.adapters.extraction import (
    PyMuPDFExtractor,
    PyMuPDFFigureCropper,
    graphic_boxes,
)
from flashcards.adapters.memory import (
    InMemoryAiJobRepository,
    InMemoryDraftCardRepository,
    InMemoryMediaRepository,
    InMemoryObjectStore,
    InMemoryQuotaSettingsRepository,
    InMemorySpendHoldRepository,
    InMemoryUsageRepository,
    InMemoryUserRepository,
)
from flashcards.application import card_generation_service
from flashcards.application.ai_spend import AiSpend
from flashcards.application.card_generation_service import CardGenerationService
from flashcards.domain.ai import (
    DRAFT_CARDS_SCHEMA,
    OutputTruncated,
    StructuredRequest,
    StructuredResult,
    TokenUsage,
    parse_draft_output,
)
from flashcards.domain.figures import (
    FIGURE_MARGIN,
    FigureBox,
    InvalidFigure,
    figure_markdown,
    snap,
    with_figure,
)
from flashcards.domain.jobs import JobFailure, JobStatus
from flashcards.domain.notes import NoteType
from flashcards.domain.quotas import InstanceQuotas, billing_period
from flashcards.domain.sources import ExtractedPage, PageKind, SourceRef, UnreadableDocument
from flashcards.domain.usage import TokenPrices

# --- the box --------------------------------------------------------------


def test_a_box_must_be_a_non_empty_rectangle_within_the_page() -> None:
    assert FigureBox(0, 0, 1000, 1000).area == 1_000_000
    for edges in ((10, 0, 10, 5), (0, 9, 5, 3), (-1, 0, 5, 5), (0, 0, 1001, 5)):
        with pytest.raises(InvalidFigure):
            FigureBox(*edges)


def test_padding_grows_a_box_but_never_past_the_page() -> None:
    assert FigureBox(100, 200, 300, 400).padded() == FigureBox(
        100 - FIGURE_MARGIN, 200 - FIGURE_MARGIN, 300 + FIGURE_MARGIN, 400 + FIGURE_MARGIN
    )
    assert FigureBox(10, 5, 990, 999).padded() == FigureBox(0, 0, 1000, 1000)


def test_overlap_is_intersection_over_union() -> None:
    box = FigureBox(0, 0, 100, 100)
    assert box.overlap(box) == 1.0
    assert box.overlap(FigureBox(50, 0, 150, 100)) == pytest.approx(1 / 3)
    assert box.overlap(FigureBox(100, 0, 200, 100)) == 0.0  # touching edges share nothing
    assert box.overlap(FigureBox(0, 300, 100, 400)) == 0.0


def test_snap_prefers_the_pdfs_own_box_when_the_guess_matches_it() -> None:
    guess = FigureBox(100, 100, 500, 500)
    exact = FigureBox(110, 90, 520, 480)
    elsewhere = FigureBox(600, 600, 900, 900)
    assert snap(guess, (elsewhere, exact)) == exact.padded(FIGURE_MARGIN // 2)
    # A guess matching nothing (or with nothing to match) keeps its own box, padded.
    assert snap(guess, (elsewhere,)) == guess.padded()
    assert snap(guess, ()) == guess.padded()


def test_a_figure_goes_first_on_the_question_side() -> None:
    media = "m-1"
    image = figure_markdown(media)
    assert image == "![Figure](media:m-1)"
    assert with_figure(NoteType.BASIC, (("front", "Which line?"), ("back", "y = 2x")), media) == (
        ("front", f"{image}\n\nWhich line?"),
        ("back", "y = 2x"),
    )
    assert with_figure(NoteType.CLOZE, (("text", "It is {{c1::linear}}."),), media) == (
        ("text", f"{image}\n\nIt is {{{{c1::linear}}}}."),
    )


# --- the model's output ---------------------------------------------------


def _card(figure: object = 0) -> dict[str, object]:
    return {"type": "basic", "fields": {"front": "Q", "back": "A"}, "figure": figure}


def _box(**overrides: object) -> dict[str, object]:
    return {"left": 10, "top": 20, "right": 300, "bottom": 400, **overrides}


def test_output_with_figures_parses_into_cards_and_boxes() -> None:
    output = parse_draft_output(
        {"cards": [_card(1), _card(0), _card(2)], "figures": [_box(), _box(left=500, right=900)]},
        source_page=4,
    )
    assert [card.figure for card in output.cards] == [1, 0, 2]
    assert output.figures == (FigureBox(10, 20, 300, 400), FigureBox(500, 20, 900, 400))
    assert all(card.source_page == 4 for card in output.cards)


def test_output_without_figure_keys_has_no_figures() -> None:
    output = parse_draft_output(
        {"cards": [{"type": "basic", "fields": {"front": "Q", "back": "A"}}]}, source_page=1
    )
    assert output.figures == () and output.cards[0].figure == 0


@pytest.mark.parametrize(
    ("figures", "expected"),
    [
        ({"left": 1}, ()),  # not a list: no figures
        (["box", _box(top=1.5), _box(right=False), {"left": 1, "top": 2, "right": 3}], (None,) * 4),
        ([_box(right=5), _box(right=1200)], (None, None)),  # not a rectangle within the page
        ([_box()] * 13, (FigureBox(10, 20, 300, 400),) * 12),  # past MAX_FIGURES: dropped
    ],
)
def test_unusable_figures_are_dropped_keeping_their_numbers(
    figures: object, expected: tuple[FigureBox | None, ...]
) -> None:
    output = parse_draft_output({"cards": [_card()], "figures": figures}, source_page=1)
    assert output.figures == expected and len(output.cards) == 1


@pytest.mark.parametrize("figure", [1, 2, 3, -1, True, "1", None])
def test_a_card_naming_a_missing_or_unusable_figure_keeps_its_text(figure: object) -> None:
    raw = {"cards": [_card(figure)], "figures": [_box(right=5), _box(right=1500)]}
    (card,) = parse_draft_output(raw, source_page=1).cards
    assert card.figure == 0 and dict(card.fields) == {"front": "Q", "back": "A"}


def test_a_page_with_nothing_to_study_has_no_cards() -> None:
    assert parse_draft_output({"cards": [], "figures": []}, source_page=1).cards == ()


def test_every_card_shape_requires_its_figure() -> None:
    cards = DRAFT_CARDS_SCHEMA["properties"]
    assert isinstance(cards, dict)
    shapes = cards["cards"]["items"]["anyOf"]
    assert all("figure" in shape["required"] for shape in shapes)
    assert DRAFT_CARDS_SCHEMA["required"] == ["cards", "figures"]


# --- finding graphics in a PDF --------------------------------------------

_TEXT = "Section 11.7 Systems of inequalities, worked examples for the chapter"


def _graph_pdf() -> bytes:
    """A text page with a vector graph: a square grid with two labels."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), _TEXT)
    for step in range(0, 201, 20):
        page.draw_line((300 + step, 200), (300 + step, 400))
        page.draw_line((300, 200 + step), (500, 200 + step))
    page.insert_text((505, 300), "x")
    page.insert_text((400, 195), "y")
    data: bytes = doc.tobytes()
    doc.close()
    return data


def _found(data: bytes, index: int = 0) -> tuple[bool, tuple[FigureBox, ...]]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return graphic_boxes(doc[index])
    finally:
        doc.close()


def test_a_vector_graph_is_a_figure_with_its_exact_box() -> None:
    has_graphics, figures = _found(_graph_pdf())
    assert has_graphics
    (box,) = figures
    # The grid spans x 300..500 and y 200..400 points on a 595 x 842 (A4) page.
    assert box.left == pytest.approx(504, abs=3) and box.right == pytest.approx(840, abs=3)
    assert box.top == pytest.approx(237, abs=3) and box.bottom == pytest.approx(475, abs=3)


def test_a_frame_around_text_and_small_marks_are_not_figures() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    box = pymupdf.Rect(72, 100, 540, 300)
    page.draw_rect(box)
    page.insert_textbox(box, ("A table cell full of words, " * 40), fontsize=11)
    page.draw_line((72, 600), (90, 600))  # an underline
    data = doc.tobytes()
    doc.close()
    assert _found(data) == (False, ())


def _picture(width: int, height: int, shade: int = 200) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.clear_with(shade)
    data: bytes = pix.tobytes("png")
    return data


def test_an_embedded_picture_is_a_figure() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), _TEXT)
    page.insert_image(pymupdf.Rect(100, 200, 400, 500), stream=_picture(40, 40))
    data = doc.tobytes()
    doc.close()
    has_graphics, figures = _found(data)
    assert has_graphics and len(figures) == 1


def test_a_small_picture_or_one_off_the_page_is_not_a_figure() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), _TEXT)
    page.insert_image(pymupdf.Rect(100, 200, 120, 220), stream=_picture(40, 40))  # an icon
    page.insert_image(pymupdf.Rect(700, 900, 800, 1000), stream=_picture(40, 40))  # off the page
    data = doc.tobytes()
    doc.close()
    assert _found(data) == (False, ())


def test_a_scan_under_a_text_layer_has_graphics_but_a_slide_background_does_not() -> None:
    doc = pymupdf.open()
    for picture in (_picture(900, 1200), _picture(40, 40)):  # about 109 and 5 dpi
        page = doc.new_page()
        page.insert_image(page.rect, stream=picture, keep_proportion=False)
        page.insert_text((72, 72), _TEXT)
    data = doc.tobytes()
    doc.close()
    # A scan is no figure to crop, but the model must see the page.
    assert _found(data, 0) == (True, ())
    assert _found(data, 1) == (False, ())


def test_a_page_wide_fill_is_ignored_and_the_figure_on_it_kept() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    page.insert_text((72, 72), _TEXT)
    for step in range(0, 201, 20):
        page.draw_line((300 + step, 200), (300 + step, 400))
        page.draw_line((300, 200 + step), (500, 200 + step))
    data = doc.tobytes()
    doc.close()
    has_graphics, (box,) = _found(data)
    assert has_graphics and box.left == pytest.approx(504, abs=3)


def test_boxes_follow_the_page_rotation() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), _TEXT)
    page.insert_image(pymupdf.Rect(50, 100, 250, 300), stream=_picture(40, 40, shade=0))
    page.set_rotation(90)
    data = doc.tobytes()
    doc.close()
    has_graphics, (box,) = _found(data)
    rendered = pymupdf.open(stream=data, filetype="pdf")[0].get_pixmap()
    # The box's centre on the rendered (rotated) page is the black picture.
    x = (box.left + box.right) / 2000 * rendered.width
    y = (box.top + box.bottom) / 2000 * rendered.height
    assert has_graphics and rendered.pixel(int(x), int(y)) == (0, 0, 0)


async def test_a_text_page_with_a_graph_is_sent_as_text_and_image() -> None:
    extractor = PyMuPDFExtractor()
    (page,) = [p async for p in extractor.extract(_graph_pdf(), file_id="f", max_pages=5)]
    assert page.kind is PageKind.TEXT and page.text is not None and "11.7" in page.text
    assert page.image is not None and page.image.startswith(b"\x89PNG")
    assert page.image_media_type == "image/png" and len(page.figure_boxes) == 1
    # Text-only extraction (chat indexing) never renders the page.
    (text_only,) = [
        p async for p in extractor.extract(_graph_pdf(), file_id="f", max_pages=5, text_only=True)
    ]
    assert text_only.image is None and text_only.figure_boxes == ()


# --- cropping -------------------------------------------------------------


def _page_png(width: int = 600, height: int = 800) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.clear_with(255)
    data: bytes = pix.tobytes("png")
    return data


async def test_a_crop_is_the_box_at_the_images_own_resolution() -> None:
    cropped = await PyMuPDFFigureCropper().crop(_page_png(), FigureBox(500, 250, 1000, 750))
    pix = pymupdf.Pixmap(cropped)
    assert (pix.width, pix.height) == (300, 400)


async def test_a_jpeg_page_crops_too() -> None:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100), False)
    pix.clear_with(255)
    cropped = await PyMuPDFFigureCropper().crop(pix.tobytes("jpeg"), FigureBox(0, 0, 500, 1000))
    assert pymupdf.Pixmap(cropped).width == 100


async def test_an_image_claiming_too_many_pixels_is_refused_before_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extraction, "MAX_RASTER_PIXELS", 479_999)  # one short of 600 x 800
    with pytest.raises(UnreadableDocument, match="too large"):
        await PyMuPDFFigureCropper().crop(_page_png(), FigureBox(0, 0, 10, 10))


@pytest.mark.parametrize("data", [b"%PDF-1.7 a document", b"GIF89a", b"\x89PNG not really"])
async def test_only_a_readable_png_or_jpeg_is_cropped(data: bytes) -> None:
    with pytest.raises(UnreadableDocument):
        await PyMuPDFFigureCropper().crop(data, FigureBox(0, 0, 10, 10))


# --- generation with figures ---------------------------------------------

_PAGE_IMAGE = b"\x89PNG-page"


class FigureLLM:
    """Answers with two cards showing figure 1, one showing none, and one figure."""

    def __init__(self) -> None:
        self.seen: list[StructuredRequest] = []

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        self.seen.append(request)
        cards: list[object] = [
            {
                "type": "basic",
                "fields": {"front": "Which side is shaded?", "back": "Below"},
                "figure": 1,
            },
            {"type": "cloze", "fields": {"text": "The line is {{c1::dashed}}."}, "figure": 1},
            {"type": "basic", "fields": {"front": "Q", "back": "A"}, "figure": 0},
        ]
        figures: list[object] = [{"left": 100, "top": 100, "right": 500, "bottom": 500}]
        return StructuredResult(
            data={"cards": cards, "figures": figures},
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            model="fake",
        )


class FakeCropper:
    def __init__(self, error: Exception | None = None) -> None:
        self.boxes: list[FigureBox] = []
        self._error = error

    async def crop(self, image: bytes, box: FigureBox) -> bytes:
        self.boxes.append(box)
        if self._error is not None:
            raise self._error
        return b"\x89PNG-crop"


def _page(*, kind: PageKind = PageKind.IMAGE, image: bytes | None = _PAGE_IMAGE) -> ExtractedPage:
    return ExtractedPage(
        ref=SourceRef(file_id="src", page=1),
        kind=kind,
        text="Graph y < 2x - 5" if kind is PageKind.TEXT else None,
        image=image,
        image_media_type="image/jpeg" if image is not None else None,
        figure_boxes=(FigureBox(110, 90, 520, 480),),
    )


async def _generate(
    page: ExtractedPage,
    *,
    media: InMemoryMediaRepository | None,
    cropper: FakeCropper | None,
) -> tuple[list[dict[str, str]], FigureLLM]:
    jobs, drafts = InMemoryAiJobRepository(), InMemoryDraftCardRepository()
    store = InMemoryObjectStore()
    llm = FigureLLM()
    clock = FakeClock()
    service = CardGenerationService(
        jobs, drafts, store, FakeExtractor([page]), llm, clock, RecordingUnitOfWork(),
        _spend(clock), users=InMemoryUserRepository(), media=media, cropper=cropper,
    )
    job_id = await _seed_job(jobs, store)
    await service.run(job_id)
    job = await jobs.get(job_id)
    assert job is not None and job.status is JobStatus.SUCCEEDED
    return [dict(d.fields) for d in await drafts.list_for_job(job_id)], llm


async def test_a_figure_is_cropped_once_stored_and_shown_on_each_card() -> None:
    media, cropper = InMemoryMediaRepository(), FakeCropper()
    fields, _llm = await _generate(_page(), media=media, cropper=cropper)
    (asset,) = await media.list_for_owner(ALICE)
    assert asset.data == b"\x89PNG-crop" and asset.content_type == "image/png"
    # One crop for the two cards that show it, snapped to the page's own box.
    assert cropper.boxes == [FigureBox(110, 90, 520, 480).padded(FIGURE_MARGIN // 2)]
    image = figure_markdown(asset.id)
    assert fields[0]["front"] == f"{image}\n\nWhich side is shaded?"
    assert fields[1]["text"] == f"{image}\n\nThe line is {{{{c1::dashed}}}}."
    assert fields[2] == {"front": "Q", "back": "A"}


async def test_a_text_page_with_graphics_is_sent_as_text_then_image() -> None:
    _fields, llm = await _generate(
        _page(kind=PageKind.TEXT), media=InMemoryMediaRepository(), cropper=FakeCropper()
    )
    documents = llm.seen[0].documents
    assert [d.media_type for d in documents] == ["text/plain", "image/jpeg"]
    assert documents[0].data == b"Graph y < 2x - 5" and documents[1].data == _PAGE_IMAGE


async def test_a_figure_that_cannot_be_cropped_is_left_out() -> None:
    media = InMemoryMediaRepository()
    fields, _llm = await _generate(
        _page(), media=media, cropper=FakeCropper(UnreadableDocument("too large"))
    )
    assert await media.list_for_owner(ALICE) == []
    assert fields[0]["front"] == "Which side is shaded?"


async def test_a_crop_over_the_media_size_limit_is_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(card_generation_service, "MAX_MEDIA_BYTES", 3)
    media = InMemoryMediaRepository()
    fields, _llm = await _generate(_page(), media=media, cropper=FakeCropper())
    assert await media.list_for_owner(ALICE) == []
    assert fields[1]["text"] == "The line is {{c1::dashed}}."


async def test_cards_keep_their_text_without_an_image_a_cropper_or_a_media_store() -> None:
    media = InMemoryMediaRepository()
    text_page = _page(kind=PageKind.TEXT, image=None)
    no_image, _ = await _generate(text_page, media=media, cropper=FakeCropper())
    no_cropper, _ = await _generate(_page(), media=media, cropper=None)
    assert await media.list_for_owner(ALICE) == []
    no_store, _ = await _generate(_page(), media=None, cropper=FakeCropper())
    for fields in (no_image, no_cropper, no_store):
        assert fields[0]["front"] == "Which side is shaded?"


class TruncatingLLM:
    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        raise OutputTruncated(TokenUsage(input_tokens=900, output_tokens=16_000), "fake-model")


async def test_a_truncated_page_records_what_the_provider_billed_and_fails() -> None:
    jobs, drafts = InMemoryAiJobRepository(), InMemoryDraftCardRepository()
    store, usage = InMemoryObjectStore(), InMemoryUsageRepository()
    clock = FakeClock()
    spend = AiSpend(
        usage, InMemoryQuotaSettingsRepository(), clock,
        InMemorySpendHoldRepository(usage), provider="test",
        prices=TokenPrices(input_usd_per_mtok=1.0, output_usd_per_mtok=1.0),
        defaults=InstanceQuotas(),
    )
    service = CardGenerationService(
        jobs, drafts, store, FakeExtractor([_page()]), TruncatingLLM(), clock,
        RecordingUnitOfWork(), spend, users=InMemoryUserRepository(),
    )
    job_id = await _seed_job(jobs, store)
    await service.run(job_id)
    job = await jobs.get(job_id)
    assert job is not None and job.status is JobStatus.FAILED
    assert job.error == JobFailure.BAD_MODEL_OUTPUT.value
    (row,) = await usage.by_user(billing_period(job.created_at))
    assert (row.input_tokens, row.output_tokens) == (900, 16_000)
