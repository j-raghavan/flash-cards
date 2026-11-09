# SPDX-License-Identifier: AGPL-3.0-only
"""Load the operator's legal pages from a directory at startup (F-LEGAL-1).

The directory holds privacy.md, terms.md and imprint.md. A missing file falls
back to the bundled template; a file that is there but unusable (too large, not
UTF-8, empty) stops startup with the reason, so a mistake is caught on deploy
rather than served. The file names are fixed, so a request can never choose a path.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flashcards.domain.legal import (
    MAX_LEGAL_PAGE_BYTES,
    InvalidLegalPage,
    LegalDocument,
    LegalPage,
    operator_document,
    template_document,
)

logger = logging.getLogger(__name__)


def load_legal_pages(directory: str | None) -> dict[LegalPage, LegalDocument]:
    # An empty value (FLASHCARDS_LEGAL_DIR=) means unset, not the working directory.
    if directory is None or not directory.strip():
        logger.warning(
            "FLASHCARDS_LEGAL_DIR is not set: the privacy, terms and imprint pages "
            "show unfilled templates."
        )
        return {page: template_document(page) for page in LegalPage}
    root = Path(directory)
    if not root.is_dir():
        raise InvalidLegalPage(f"FLASHCARDS_LEGAL_DIR {directory!r} is not a directory")
    return {page: _load(root, page) for page in LegalPage}


def _load(root: Path, page: LegalPage) -> LegalDocument:
    path = root / page.file_name
    # A dangling symlink is a deploy mistake, not a missing page: exists() is False
    # for it, so check the link itself too.
    if not path.exists() and not path.is_symlink():
        logger.warning("%s is missing; serving the unfilled template.", path)
        return template_document(page)
    if not path.is_file():
        raise InvalidLegalPage(f"{path} is not a regular file")
    with path.open("rb") as handle:
        data = handle.read(MAX_LEGAL_PAGE_BYTES + 1)  # enough to tell "too large"
    return operator_document(page, data)
