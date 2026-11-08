# SPDX-License-Identifier: AGPL-3.0-only
"""AI card-generation routes (F-AI-1, F-AI-4, F-AI-10, F-AI-11).

Upload a document to start an async generation job, poll its status, then review
and approve the draft cards it produced into a real deck.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, current_user, get_container
from flashcards.api.schemas import (
    ApproveDraftsRequest,
    CardOut,
    DraftOut,
    JobOut,
    TranscriptGenerateRequest,
    YouTubeGenerateRequest,
)
from flashcards.domain.ai import Thoroughness

router = APIRouter(prefix="/ai", tags=["ai"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@router.post("/generate", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
async def start_generation(
    file: UploadFile = File(...),
    deck_id: str = Form(...),
    thoroughness: Thoroughness = Form(Thoroughness.STANDARD),
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> JobOut:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File too large."
        )
    job = await container.generation.start(
        owner_id=caller.user.id, deck_id=deck_id, data=data, thoroughness=thoroughness
    )
    return JobOut.from_job(job)


@router.post(
    "/generate/youtube", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut
)
async def start_youtube_generation(
    body: YouTubeGenerateRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> JobOut:
    job = await container.transcript_import.from_youtube(
        owner_id=caller.user.id,
        deck_id=body.deck_id,
        url=body.url,
        thoroughness=body.thoroughness,
    )
    return JobOut.from_job(job)


@router.post(
    "/generate/transcript", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut
)
async def start_transcript_generation(
    body: TranscriptGenerateRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> JobOut:
    job = await container.transcript_import.from_text(
        owner_id=caller.user.id,
        deck_id=body.deck_id,
        text=body.transcript,
        thoroughness=body.thoroughness,
    )
    return JobOut.from_job(job)


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(
    job_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> JobOut:
    return JobOut.from_job(await container.generation.get(owner_id=caller.user.id, job_id=job_id))


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    job_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> JobOut:
    await container.generation.cancel(owner_id=caller.user.id, job_id=job_id)
    return JobOut.from_job(await container.generation.get(owner_id=caller.user.id, job_id=job_id))


@router.get("/jobs/{job_id}/drafts", response_model=list[DraftOut])
async def list_drafts(
    job_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> list[DraftOut]:
    drafts = await container.draft_review.list_drafts(owner_id=caller.user.id, job_id=job_id)
    return [DraftOut.from_record(draft) for draft in drafts]


@router.post(
    "/jobs/{job_id}/approve",
    status_code=status.HTTP_201_CREATED,
    response_model=list[CardOut],
)
async def approve_drafts(
    job_id: str,
    body: ApproveDraftsRequest,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> list[CardOut]:
    cards = await container.draft_review.approve(
        owner_id=caller.user.id, job_id=job_id, draft_ids=body.draft_ids
    )
    return [CardOut.from_card(card) for card in cards]
