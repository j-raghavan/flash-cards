# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime

import pymupdf
import pytest

from flashcards.adapters.media import PuremagicMediaSniffer
from flashcards.adapters.memory import InMemoryMediaRepository
from flashcards.application.media_service import MediaService
from flashcards.domain.media import MediaNotFound, MediaTooLarge, UnsupportedMedia

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ALICE = "alice"
BOB = "bob"


class FakeClock:
    def now(self) -> datetime:
        return NOW


def _png() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=32, height=32)
    try:
        return bytes(page.get_pixmap().tobytes("png"))
    finally:
        doc.close()


def _pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page()
    try:
        return bytes(doc.tobytes())
    finally:
        doc.close()


def _service(max_bytes: int = 25 * 1024 * 1024) -> MediaService:
    return MediaService(
        InMemoryMediaRepository(), PuremagicMediaSniffer(), FakeClock(), max_bytes=max_bytes
    )


def test_sniffer_accepts_png_and_rejects_others() -> None:
    sniffer = PuremagicMediaSniffer()
    assert sniffer.sniff(_png()) == "image/png"
    with pytest.raises(UnsupportedMedia):
        sniffer.sniff(_pdf())  # a PDF is not a card media type
    with pytest.raises(UnsupportedMedia):
        sniffer.sniff(b"\x00\x01 not identifiable")


async def test_upload_stores_a_sniffed_asset() -> None:
    service = _service()
    data = _png()
    asset = await service.upload(owner_id=ALICE, data=data)
    assert asset.owner_id == ALICE
    assert asset.content_type == "image/png"
    assert asset.data == data
    fetched = await service.get(owner_id=ALICE, media_id=asset.id)
    assert fetched.id == asset.id and fetched.data == data


async def test_upload_rejects_oversized() -> None:
    service = _service(max_bytes=10)
    with pytest.raises(MediaTooLarge):
        await service.upload(owner_id=ALICE, data=_png())


async def test_upload_rejects_unsupported_type() -> None:
    service = _service()
    with pytest.raises(UnsupportedMedia):
        await service.upload(owner_id=ALICE, data=_pdf())


async def test_get_is_owner_scoped() -> None:
    service = _service()
    asset = await service.upload(owner_id=ALICE, data=_png())
    with pytest.raises(MediaNotFound):
        await service.get(owner_id=BOB, media_id=asset.id)
    with pytest.raises(MediaNotFound):
        await service.get(owner_id=ALICE, media_id="missing")
