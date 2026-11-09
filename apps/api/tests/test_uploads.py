# SPDX-License-Identifier: AGPL-3.0-only
"""Uploads are read in chunks and refused as soon as they pass the cap."""

from __future__ import annotations

import io

import pytest
from fastapi import HTTPException, UploadFile

from flashcards.api import uploads
from flashcards.api.uploads import read_capped


class _CountingFile(io.BytesIO):
    """Records how many bytes the reader asked for."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.bytes_read = 0

    def read(self, size: int | None = -1, /) -> bytes:
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk


async def test_reads_an_upload_within_the_cap() -> None:
    data = b"x" * 2500
    assert await read_capped(UploadFile(io.BytesIO(data)), 2500) == data


async def test_stops_reading_once_past_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uploads, "CHUNK_BYTES", 100)
    source = _CountingFile(b"x" * 10_000)
    with pytest.raises(HTTPException) as caught:
        await read_capped(UploadFile(source), 250)  # size unknown: counted while reading
    assert caught.value.status_code == 413
    assert source.bytes_read == 300  # three chunks, never the whole upload


async def test_refuses_without_reading_when_the_size_is_known() -> None:
    source = _CountingFile(b"x" * 10_000)
    with pytest.raises(HTTPException) as caught:
        await read_capped(UploadFile(source, size=10_000), 250)
    assert caught.value.status_code == 413
    assert source.bytes_read == 0


async def test_an_empty_upload_is_empty() -> None:
    assert await read_capped(UploadFile(io.BytesIO(b""), size=0), 10) == b""
