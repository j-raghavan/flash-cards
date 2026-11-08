# SPDX-License-Identifier: AGPL-3.0-only
"""Media routes: upload and serve card attachments (F-CARD-5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from pydantic import BaseModel

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, current_user, get_container

router = APIRouter(prefix="/media", tags=["media"])


class MediaOut(BaseModel):
    id: str
    content_type: str


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MediaOut)
async def upload_media(
    file: UploadFile = File(...),
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> MediaOut:
    asset = await container.media.upload(owner_id=caller.user.id, data=await file.read())
    return MediaOut(id=asset.id, content_type=asset.content_type)


@router.get("/{media_id}")
async def get_media(
    media_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> Response:
    asset = await container.media.get(owner_id=caller.user.id, media_id=media_id)
    return Response(content=asset.data, media_type=asset.content_type)
