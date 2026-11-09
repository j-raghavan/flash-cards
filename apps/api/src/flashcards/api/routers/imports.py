# SPDX-License-Identifier: AGPL-3.0-only
"""Import routes: upload an Anki .apkg (F-IMP-1..3, ADR 0015)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status

from flashcards.api.container import Container
from flashcards.api.deps import CurrentUser, current_user, get_container
from flashcards.api.schemas import ImportReportOut
from flashcards.api.uploads import read_capped
from flashcards.domain.anki import MAX_SELECTION_ITEMS, MAX_SELECTION_LENGTH, ImportSelection

router = APIRouter(prefix="/import", tags=["import"])

# Bounded low: a zip's central directory is parsed in full at open time, so a huge
# upload of tiny entries can allocate many ZipInfo objects before the reader's
# entry-count guard runs. A smaller cap bounds that worst case.
MAX_UPLOAD_BYTES = 40 * 1024 * 1024


def _selection(decks: list[str], tags: list[str]) -> ImportSelection:
    chosen = [value.strip() for value in decks], [value.strip() for value in tags]
    for values in chosen:
        if len(values) > MAX_SELECTION_ITEMS or any(
            not 0 < len(value) <= MAX_SELECTION_LENGTH for value in values
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Select at most {MAX_SELECTION_ITEMS} decks and tags, each 1 to "
                    f"{MAX_SELECTION_LENGTH} characters."
                ),
            )
    return ImportSelection(decks=tuple(chosen[0]), tags=tuple(chosen[1]))


@router.post("/apkg", status_code=status.HTTP_201_CREATED, response_model=ImportReportOut)
async def import_apkg(
    response: Response,
    file: UploadFile = File(...),
    dry_run: bool = Form(default=False),
    deck: list[str] = Form(default_factory=list),
    tag: list[str] = Form(default_factory=list),
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> ImportReportOut:
    """Import a package, or with dry_run report what an import would do, writing nothing.

    Repeat `deck` to import only those decks (with their sub-decks) and `tag` to
    import only notes carrying one of those tags (or a descendant tag).
    """
    selection = _selection(deck, tag)
    data = await read_capped(file, MAX_UPLOAD_BYTES)
    report = await container.apkg_import.import_package(
        owner_id=caller.user.id, data=data, selection=selection, dry_run=dry_run
    )
    if dry_run:
        response.status_code = status.HTTP_200_OK
    return ImportReportOut.from_report(report)
