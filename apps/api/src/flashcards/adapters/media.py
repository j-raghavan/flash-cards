# SPDX-License-Identifier: AGPL-3.0-only
"""Media type sniffing from bytes (F-CARD-5, NF-SEC-2)."""

from __future__ import annotations

import puremagic

from flashcards.domain.media import ALLOWED_MEDIA_TYPES, UnsupportedMedia


class PuremagicMediaSniffer:
    def sniff(self, data: bytes) -> str:
        try:
            media = str(puremagic.from_string(data, mime=True))
        except puremagic.PureError as exc:
            raise UnsupportedMedia("could not identify media type") from exc
        if media not in ALLOWED_MEDIA_TYPES:
            raise UnsupportedMedia(media)
        return media
