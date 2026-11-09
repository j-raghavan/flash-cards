# SPDX-License-Identifier: AGPL-3.0-only
"""Media routes: upload and serve card attachments (F-CARD-5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from pydantic import BaseModel

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, current_user, get_container
from flashcards.api.uploads import read_capped

router = APIRouter(prefix="/media", tags=["media"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Private, immutable (addressed by a random id), and never sniffed into markup.
_SERVE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Disposition": "inline",
    "Cache-Control": "private, max-age=31536000, immutable",
}


class MediaOut(BaseModel):
    id: str
    content_type: str


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MediaOut)
async def upload_media(
    file: UploadFile = File(...),
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> MediaOut:
    data = await read_capped(file, MAX_UPLOAD_BYTES)
    asset = await container.media.upload(owner_id=caller.user.id, data=data)
    return MediaOut(id=asset.id, content_type=asset.content_type)


@router.get("/{media_id}")
async def get_media(
    media_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    asset = await container.media.get(owner_id=caller.user.id, media_id=media_id)
    return Response(content=asset.data, media_type=asset.content_type, headers=_SERVE_HEADERS)
