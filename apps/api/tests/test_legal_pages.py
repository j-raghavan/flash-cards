# SPDX-License-Identifier: AGPL-3.0-only
"""Privacy policy, terms and imprint from the operator's directory (F-LEGAL-1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flashcards.adapters.legal_pages import load_legal_pages
from flashcards.api.app import create_app
from flashcards.api.routers.legal import LEGAL_CACHE_CONTROL
from flashcards.config import Settings
from flashcards.domain.legal import (
    MAX_LEGAL_PAGE_BYTES,
    InvalidLegalPage,
    LegalPage,
    operator_document,
    template_document,
)


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate(
        {"environment": "test", "session_cookie_secure": False, **overrides}
    )


# --- domain ---


def test_every_template_says_the_operator_must_fill_it_in() -> None:
    for page in LegalPage:
        document = template_document(page)
        assert document.is_template
        assert "has not filled in this page yet" in document.markdown
        assert "*Operator:" in document.markdown


def test_pages_have_fixed_file_names_and_headings() -> None:
    assert [page.file_name for page in LegalPage] == ["privacy.md", "terms.md", "imprint.md"]
    assert LegalPage.IMPRINT.heading == "Imprint"


def test_an_operator_page_keeps_its_text_and_drops_a_byte_order_mark() -> None:
    document = operator_document(LegalPage.TERMS, "﻿# Terms\n\nBe kind.".encode())
    assert document.markdown == "# Terms\n\nBe kind."
    assert not document.is_template


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"x" * (MAX_LEGAL_PAGE_BYTES + 1), "larger"),
        (b"\xff\xfe\x00bad", "UTF-8"),
        (b"  \n\t", "empty"),
        (b"# Title\x00", "NUL"),
    ],
)
def test_an_unusable_operator_page_is_refused(data: bytes, reason: str) -> None:
    with pytest.raises(InvalidLegalPage, match=reason):
        operator_document(LegalPage.PRIVACY, data)


# --- loading ---


@pytest.mark.parametrize("directory", [None, "", "  "])
def test_without_a_directory_every_page_is_a_template(directory: str | None) -> None:
    pages = load_legal_pages(directory)
    assert set(pages) == set(LegalPage)
    assert all(document.is_template for document in pages.values())


def test_a_dangling_symlink_stops_startup(tmp_path: Path) -> None:
    (tmp_path / "privacy.md").symlink_to(tmp_path / "nowhere.md")
    with pytest.raises(InvalidLegalPage, match="not a regular file"):
        load_legal_pages(str(tmp_path))


def test_missing_files_fall_back_to_templates(tmp_path: Path) -> None:
    (tmp_path / "imprint.md").write_text("# Imprint\n\nJane Doe, Example Street 1", "utf-8")
    pages = load_legal_pages(str(tmp_path))
    assert not pages[LegalPage.IMPRINT].is_template
    assert pages[LegalPage.IMPRINT].markdown.endswith("Example Street 1")
    assert pages[LegalPage.PRIVACY].is_template
    assert pages[LegalPage.TERMS].is_template


def test_a_directory_that_does_not_exist_stops_startup(tmp_path: Path) -> None:
    with pytest.raises(InvalidLegalPage, match="not a directory"):
        load_legal_pages(str(tmp_path / "missing"))


def test_a_page_that_is_not_a_file_stops_startup(tmp_path: Path) -> None:
    (tmp_path / "terms.md").mkdir()
    with pytest.raises(InvalidLegalPage, match="not a regular file"):
        load_legal_pages(str(tmp_path))


def test_an_oversized_page_stops_startup(tmp_path: Path) -> None:
    (tmp_path / "privacy.md").write_bytes(b"a" * (MAX_LEGAL_PAGE_BYTES + 10))
    with pytest.raises(InvalidLegalPage, match="larger"):
        create_app(_settings(legal_dir=str(tmp_path)))


# --- HTTP ---


def test_pages_are_public_with_their_markdown(tmp_path: Path) -> None:
    (tmp_path / "privacy.md").write_text("# Privacy\n\nWe keep little.", "utf-8")
    client = TestClient(create_app(_settings(legal_dir=str(tmp_path))))

    resp = client.get("/legal/privacy")
    assert resp.status_code == 200
    assert resp.json() == {
        "page": "privacy",
        "title": "Privacy policy",
        "markdown": "# Privacy\n\nWe keep little.",
        "is_template": False,
    }
    assert resp.headers["cache-control"] == LEGAL_CACHE_CONTROL

    terms = client.get("/legal/terms").json()
    assert terms["is_template"] is True
    assert terms["title"] == "Terms of use"


@pytest.mark.parametrize("page", ["cookies", "..%2Fprivacy", "PRIVACY", "privacy.md"])
def test_only_the_known_pages_exist(page: str) -> None:
    client = TestClient(create_app(_settings()))
    assert client.get(f"/legal/{page}").status_code == 404


def test_the_ai_disclosure_is_still_served() -> None:
    client = TestClient(create_app(_settings()))
    assert client.get("/legal/ai-disclosure").json()["trains_on_user_content"] is False
