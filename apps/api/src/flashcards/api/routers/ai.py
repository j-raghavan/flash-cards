# SPDX-License-Identifier: AGPL-3.0-only
"""AI card-generation routes (F-AI-1, F-AI-4, F-AI-10, F-AI-11).

Upload a document to start an async generation job, poll its status, then review
and approve the draft cards it produced into a real deck.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import aclosing

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.sse import EventSourceResponse

from flashcards.api import job_events as events
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
from flashcards.api.uploads import read_capped
from flashcards.domain.ai import Thoroughness
from flashcards.domain.jobs import AiJob, JobNotFound

router = APIRouter(prefix="/ai", tags=["ai"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@router.post("/generate", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
async def start_generation(
    file: UploadFile = File(...),
    deck_id: str = Form(...),
    thoroughness: Thoroughness = Form(Thoroughness.STANDARD),
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> JobOut:
    data = await read_capped(file, MAX_UPLOAD_BYTES)
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
    container: Container = Depends(get_container, scope="function"),
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
    container: Container = Depends(get_container, scope="function"),
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
    container: Container = Depends(get_container, scope="function"),
) -> JobOut:
    return JobOut.from_job(await container.generation.get(owner_id=caller.user.id, job_id=job_id))


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    job_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> JobOut:
    await container.generation.cancel(owner_id=caller.user.id, job_id=job_id)
    return JobOut.from_job(await container.generation.get(owner_id=caller.user.id, job_id=job_id))


@router.get(
    "/jobs/{job_id}/events",
    # The handler returns the stream itself (after checking ownership in its own
    # short transaction), so it does not use FastAPI's yield-style SSE routes.
    responses={
        200: {
            "description": "progress and done events",
            "content": {"text/event-stream": {}},
        },
        204: {"description": "The job has already finished; read GET /ai/jobs/{id}"},
        404: {"description": "No such job"},
        429: {"description": "Too many open event streams"},
    },
)
async def job_progress_events(
    job_id: str,
    request: Request,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    """Stream the job's progress (ADR 0012).

    Ownership is checked here, in this request's own short transaction, which
    ends before streaming starts. The stream then reads the job in a fresh unit
    of work on every poll, so no database connection is held while it runs.
    """
    owner_id = caller.user.id
    job = await container.generation.get(owner_id=owner_id, job_id=job_id)  # JobNotFound: 404
    if job.is_terminal:
        # Nothing more will happen. 204 also tells EventSource not to reconnect,
        # which it otherwise does every few seconds after a stream ends.
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    slots: events.StreamSlots = request.app.state.event_stream_slots
    token = slots.acquire(owner_id)
    if token is None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many open event streams.")
    provider = request.app.state.services_provider

    async def load() -> AiJob | None:
        found: AiJob | None = None
        # aclosing: the unit of work is closed (and its session released) even when
        # the client disconnects in the middle of a poll.
        async with aclosing(provider()) as units:
            async for unit in units:
                try:
                    found = await unit.generation.get(owner_id=owner_id, job_id=job_id)
                except JobNotFound:
                    found = None
        return found

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for frame in events.job_events(
                load,
                poll_seconds=events.POLL_SECONDS,
                keepalive_seconds=events.KEEPALIVE_SECONDS,
                max_seconds=events.MAX_STREAM_SECONDS,
            ):
                yield frame
        finally:
            slots.release(owner_id, token)

    # X-Accel-Buffering: no stops nginx-style proxies from batching the frames.
    return EventSourceResponse(
        stream(), headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
async def retry_job(
    job_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> JobOut:
    return JobOut.from_job(await container.generation.retry(owner_id=caller.user.id, job_id=job_id))


@router.get("/jobs/{job_id}/drafts", response_model=list[DraftOut])
async def list_drafts(
    job_id: str,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
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
    container: Container = Depends(get_container, scope="function"),
) -> list[CardOut]:
    cards = await container.draft_review.approve(
        owner_id=caller.user.id, job_id=job_id, draft_ids=body.draft_ids
    )
    return [CardOut.from_card(card) for card in cards]
