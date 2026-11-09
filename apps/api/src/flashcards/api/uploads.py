# SPDX-License-Identifier: AGPL-3.0-only
"""Reading uploaded files into memory without exceeding a size cap.

The multipart parser spools each upload to a temporary file before the handler
runs. Reading that file in one call would put all of it in memory before any
size check, so the cap would bound nothing. read_capped reads in fixed chunks
and answers 413 as soon as the total passes the cap, so an upload never holds
more than about twice the cap (the chunks plus the joined result). The disk the
parser spools to is bounded by BodySizeLimitMiddleware.
"""

from __future__ import annotations

from fastapi import HTTPException, UploadFile, status

CHUNK_BYTES = 1024 * 1024


def _too_large() -> HTTPException:
    return HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File too large.")


async def read_capped(file: UploadFile, limit: int) -> bytes:
    """Return the upload's bytes, or raise a 413 once they exceed limit."""
    if file.size is not None and file.size > limit:
        raise _too_large()  # the parser already counted it: reject without reading
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(CHUNK_BYTES):
        total += len(chunk)
        if total > limit:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)
