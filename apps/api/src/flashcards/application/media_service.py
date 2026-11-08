# SPDX-License-Identifier: AGPL-3.0-only
"""Uploading and serving media attachments (F-CARD-5).

The content type is sniffed from the bytes, the size is capped, and reads are
owner-scoped so one user can never fetch another's media.
"""

from __future__ import annotations

import uuid

from flashcards.domain.media import MAX_MEDIA_BYTES, MediaAsset, MediaNotFound, MediaTooLarge
from flashcards.domain.ports import Clock, MediaRepository, MediaTypeSniffer


class MediaService:
    def __init__(
        self,
        media: MediaRepository,
        sniffer: MediaTypeSniffer,
        clock: Clock,
        max_bytes: int = MAX_MEDIA_BYTES,
    ) -> None:
        self._media = media
        self._sniffer = sniffer
        self._clock = clock
        self._max_bytes = max_bytes

    async def upload(self, *, owner_id: str, data: bytes) -> MediaAsset:
        if len(data) > self._max_bytes:
            raise MediaTooLarge(f"{len(data)} bytes exceeds the media limit")
        content_type = self._sniffer.sniff(data)  # raises UnsupportedMedia
        asset = MediaAsset(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            content_type=content_type,
            data=data,
            created_at=self._clock.now(),
        )
        await self._media.add(asset)
        return asset

    async def get(self, *, owner_id: str, media_id: str) -> MediaAsset:
        asset = await self._media.get(media_id)
        if asset is None or asset.owner_id != owner_id:
            raise MediaNotFound(media_id)
        return asset
