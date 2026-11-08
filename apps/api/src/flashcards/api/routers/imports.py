# SPDX-License-Identifier: AGPL-3.0-only
"""Import routes: upload an Anki .apkg (F-IMP-1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, current_user, get_container
from flashcards.api.schemas import ImportReportOut

router = APIRouter(prefix="/import", tags=["import"])

MAX_UPLOAD_BYTES = 100 * 1024 * 1024


@router.post("/apkg", status_code=status.HTTP_201_CREATED, response_model=ImportReportOut)
async def import_apkg(
    file: UploadFile = File(...),
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container),
) -> ImportReportOut:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File too large."
        )
    report = await container.apkg_import.import_package(owner_id=caller.user.id, data=data)
    return ImportReportOut.from_report(report)
