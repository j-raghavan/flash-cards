# SPDX-License-Identifier: AGPL-3.0-only
"""Legal disclosures (F-LEGAL-2).

Public, so a client can show the AI data-use disclosure before sign-up as well as
next to every AI feature. It is built once from configuration at startup.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from flashcards.api.ai_schemas import AiDisclosureOut
from flashcards.domain.disclosure import AiDisclosure

router = APIRouter(prefix="/legal", tags=["legal"])


@router.get("/ai-disclosure", response_model=AiDisclosureOut)
def get_ai_disclosure(request: Request) -> AiDisclosureOut:
    disclosure: AiDisclosure = request.app.state.ai_disclosure
    return AiDisclosureOut.from_disclosure(disclosure)
