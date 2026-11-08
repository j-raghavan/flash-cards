# SPDX-License-Identifier: AGPL-3.0-only
"""Media assets attached to cards: images and audio (F-CARD-5).

The content type is sniffed from the bytes and checked against an allow-list,
never trusted from the client (NF-SEC-2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

MAX_MEDIA_BYTES = 25 * 1024 * 1024

ALLOWED_MEDIA_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
    }
)


class MediaError(Exception):
    """Base class for media failures."""


class UnsupportedMedia(MediaError):
    """A media type that is not on the allow-list."""


class MediaTooLarge(MediaError):
    """An upload larger than the media size limit."""


class MediaNotFound(MediaError):
    """A media asset that does not exist or is not the caller's."""


@dataclass(frozen=True, slots=True)
class MediaAsset:
    id: str
    owner_id: str
    content_type: str
    data: bytes
    created_at: datetime
