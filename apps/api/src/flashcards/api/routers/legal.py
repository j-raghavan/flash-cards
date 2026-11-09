# SPDX-License-Identifier: AGPL-3.0-only
"""Legal pages (F-LEGAL-1) and disclosures (F-LEGAL-2).

Public, so a visitor can read them before signing up, and so a client can show
the AI data-use disclosure next to every AI feature. Both are built once from
configuration at startup.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from flashcards.api.ai_schemas import AiDisclosureOut
from flashcards.api.schemas import LegalPageOut
from flashcards.domain.disclosure import AiDisclosure
from flashcards.domain.legal import LegalDocument, LegalPage

router = APIRouter(prefix="/legal", tags=["legal"])

# The pages change only when the operator edits them and restarts, so browsers
# and proxies may keep them briefly.
LEGAL_CACHE_CONTROL = "public, max-age=300"


@router.get("/ai-disclosure", response_model=AiDisclosureOut)
def get_ai_disclosure(request: Request) -> AiDisclosureOut:
    disclosure: AiDisclosure = request.app.state.ai_disclosure
    return AiDisclosureOut.from_disclosure(disclosure)


@router.get("/{page}", response_model=LegalPageOut)
def get_legal_page(page: str, request: Request, response: Response) -> LegalPageOut:
    """Only the fixed page names exist; the name never reaches a file path."""
    try:
        known = LegalPage(page)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found.") from None
    pages: dict[LegalPage, LegalDocument] = request.app.state.legal_pages
    response.headers["Cache-Control"] = LEGAL_CACHE_CONTROL
    return LegalPageOut.from_document(pages[known])
