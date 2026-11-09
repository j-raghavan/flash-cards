# SPDX-License-Identifier: AGPL-3.0-only
"""The per-document page cap and the monthly page quota, by role (ADR 0022).

Members are held to FLASHCARDS_MAX_UPLOAD_PAGES and to their monthly page quota,
and are told the numbers when refused. Admins have neither limit, but their pages
are still counted, and the instance spend cap still applies to them. The role is
read on every request, so a demoted admin is capped from the next request on.
"""

from __future__ import annotations

import asyncio

import httpx2
import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.api.app import create_app
from flashcards.api.container import Container
from flashcards.application import chat_service
from flashcards.config import Settings
from flashcards.domain.ai import StructuredRequest, StructuredResult, TokenUsage
from flashcards.domain.invites import Role
from flashcards.domain.quotas import QuotaExceeded

PASSWORD = "correct-horse-battery"
CAP = 3


class _FakeLLM:
    def __init__(self) -> None:
        self.seen: list[StructuredRequest] = []

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        self.seen.append(request)
        return StructuredResult(
            data={"answer": "On the last page.", "citations": [1]},
            usage=TokenUsage(1, 1),
            model="fake",
        )


def _app(llm: _FakeLLM | None = None) -> FastAPI:
    settings = Settings(environment="test", session_cookie_secure=False, max_upload_pages=CAP)
    return create_app(settings, llm=llm or _FakeLLM())


def _join(app: FastAPI, role: Role, email: str) -> tuple[TestClient, str]:
    container: Container = app.state.container
    token = asyncio.run(container.invites.create(created_by="seed", role=role)).raw_token
    client = TestClient(app)
    resp = client.post("/auth/signup", json={"token": token, "email": email, "password": PASSWORD})
    assert resp.status_code == 201
    return client, str(resp.json()["id"])


def _pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Page {i + 1} explains mitochondria and ATP.")
    try:
        return bytes(doc.tobytes())
    finally:
        doc.close()


def _deck(client: TestClient) -> str:
    resp = client.post("/decks", json={"name": "Deck"})
    assert resp.status_code == 201
    return str(resp.json()["id"])


def _upload(client: TestClient, deck_id: str, pages: int) -> httpx2.Response:
    return client.post(
        "/ai/generate",
        files={"file": ("lecture.pdf", _pdf(pages), "application/pdf")},
        data={"deck_id": deck_id},
    )


def test_quota_messages() -> None:
    assert str(QuotaExceeded()) == "AI page quota exceeded for this period."
    assert str(QuotaExceeded(remaining=4)) == "AI page quota exceeded for this period."
    assert str(QuotaExceeded(pages=2, remaining=0, limit=0)) == (
        "AI generation is not included in your account this month. This document has 2 pages."
    )
    assert str(QuotaExceeded(remaining=0, limit=0)) == (
        "AI generation is not included in your account this month."
    )
    assert str(QuotaExceeded(remaining=0)).startswith("You have no AI pages left this month")
    assert str(QuotaExceeded(pages=12, remaining=3)) == (
        "This document has 12 pages, but you have only 3 pages of AI generation left this month."
    )


def test_a_member_over_the_page_cap_is_told_the_count_and_the_limit() -> None:
    client, _ = _join(_app(), Role.MEMBER, "m@example.com")
    resp = _upload(client, _deck(client), CAP + 1)
    assert resp.status_code == 413
    assert resp.json() == {"detail": f"This document has {CAP + 1} pages; the limit is {CAP}."}


def test_a_member_over_the_monthly_quota_is_told_what_is_left() -> None:
    app = _app()
    client, _ = _join(app, Role.MEMBER, "m@example.com")
    admin, _ = _join(app, Role.ADMIN, "a@example.com")
    body = {"default_monthly_pages": 4, "monthly_spend_cap_micro_usd": None}
    assert admin.put("/admin/quotas", json=body).status_code == 200
    deck_id = _deck(client)
    assert _upload(client, deck_id, 3).status_code == 202
    resp = _upload(client, deck_id, 2)
    assert resp.status_code == 429
    assert resp.json() == {
        "detail": "This document has 2 pages, but you have only 1 page of AI generation "
        "left this month."
    }
    assert _upload(client, deck_id, 1).status_code == 202
    resp = _upload(client, deck_id, 1)
    assert resp.status_code == 429
    assert resp.json()["detail"].startswith("You have no AI pages left this month")
    assert resp.json()["detail"].endswith("This document has 1 page.")


def test_a_spent_quota_stops_a_youtube_request_with_a_clear_message() -> None:
    app = _app()
    client, user_id = _join(app, Role.MEMBER, "m@example.com")
    admin, _ = _join(app, Role.ADMIN, "a@example.com")
    assert admin.put(f"/admin/users/{user_id}/quota", json={"monthly_pages": 0}).status_code == 200
    resp = client.post(
        "/ai/generate/youtube",
        json={"deck_id": _deck(client), "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert resp.status_code == 429
    assert resp.json() == {"detail": "AI generation is not included in your account this month."}


def test_an_admin_has_no_page_cap_and_no_page_quota_but_is_counted() -> None:
    app = _app()
    admin, admin_id = _join(app, Role.ADMIN, "a@example.com")
    deck_id = _deck(admin)
    # Over the cap (3) and over the default monthly quota (10) together.
    assert _upload(admin, deck_id, 12).status_code == 202
    assert _upload(admin, deck_id, 4).status_code == 202
    quota = admin.get("/account/ai-quota").json()
    assert quota["max_document_pages"] is None
    assert quota["quota_applies"] is False
    assert quota["used_pages"] == 16
    usage = admin.get("/admin/usage").json()
    assert [(u["user_id"], u["pages"]) for u in usage["by_user"]] == [(admin_id, 16)]


def test_an_admins_transcripts_are_not_held_to_the_quota() -> None:
    app = _app()
    admin, admin_id = _join(app, Role.ADMIN, "a@example.com")
    body = {"default_monthly_pages": 0, "monthly_spend_cap_micro_usd": None}
    assert admin.put("/admin/quotas", json=body).status_code == 200
    deck_id = _deck(admin)
    resp = admin.post(
        "/ai/generate/transcript", json={"deck_id": deck_id, "transcript": "Cells make ATP."}
    )
    assert resp.status_code == 202
    # No transcript fetcher is configured, so the request gets past the quota check
    # (which would answer 429) and fails at the fetch instead.
    resp = admin.post(
        "/ai/generate/youtube",
        json={"deck_id": deck_id, "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert resp.status_code == 422


def test_the_spend_cap_still_applies_to_an_admin() -> None:
    app = _app()
    admin, _ = _join(app, Role.ADMIN, "a@example.com")
    body = {"default_monthly_pages": 10, "monthly_spend_cap_micro_usd": 0}
    assert admin.put("/admin/quotas", json=body).status_code == 200
    resp = _upload(admin, _deck(admin), 1)
    assert resp.status_code == 429
    assert resp.json() == {"detail": "This instance's monthly AI budget is used up."}


def test_a_member_sees_the_page_cap_and_that_the_quota_applies() -> None:
    client, _ = _join(_app(), Role.MEMBER, "m@example.com")
    quota = client.get("/account/ai-quota").json()
    assert quota["max_document_pages"] == CAP
    assert quota["quota_applies"] is True


def test_a_demoted_admin_is_capped_from_the_next_request() -> None:
    app = _app()
    first, _ = _join(app, Role.ADMIN, "first@example.com")
    second, second_id = _join(app, Role.ADMIN, "second@example.com")
    deck_id = _deck(second)
    assert _upload(second, deck_id, CAP + 1).status_code == 202
    assert first.patch(f"/admin/users/{second_id}", json={"role": "member"}).status_code == 200
    # Same session, next request: now a member.
    resp = _upload(second, deck_id, CAP + 1)
    assert resp.status_code == 413
    quota = second.get("/account/ai-quota").json()
    assert quota["max_document_pages"] == CAP and quota["quota_applies"] is True


def test_chat_indexes_an_admins_long_source_but_not_after_demotion() -> None:
    llm = _FakeLLM()
    app = _app(llm)
    first, _ = _join(app, Role.ADMIN, "first@example.com")
    second, second_id = _join(app, Role.ADMIN, "second@example.com")
    long_deck = _deck(second)
    assert _upload(second, long_deck, CAP + 2).status_code == 202
    other_deck = _deck(second)
    assert _upload(second, other_deck, CAP + 2).status_code == 202

    # As an admin the whole source is indexed, the pages past the cap included:
    # the best passage sent to the model is page 5's.
    resp = second.post(f"/decks/{long_deck}/chat", json={"question": "Page 5 mitochondria?"})
    assert resp.status_code == 200
    assert resp.json()["citations"][0]["page"] == CAP + 2
    context = llm.seen[-1].documents[0].data.decode("utf-8")
    assert context.startswith("[1] (source ") and f"page {CAP + 2})\nPage {CAP + 2}" in context

    # Demoted, the other (not yet indexed) source is over the member cap, so it
    # is recorded as having no text and the deck has nothing to chat about.
    assert first.patch(f"/admin/users/{second_id}", json={"role": "member"}).status_code == 200
    resp = second.post(f"/decks/{other_deck}/chat", json={"question": "mitochondria?"})
    assert resp.status_code == 409


def test_chat_indexing_is_bounded_for_an_admin_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_service, "MAX_CHAT_INDEX_PAGES", CAP + 1)
    admin, _ = _join(_app(), Role.ADMIN, "a@example.com")
    deck_id = _deck(admin)
    assert _upload(admin, deck_id, CAP + 2).status_code == 202
    # Over the chat ceiling: recorded as having no text, so nothing to chat about.
    resp = admin.post(f"/decks/{deck_id}/chat", json={"question": "mitochondria?"})
    assert resp.status_code == 409


def test_a_demoted_admins_counted_pages_stay_on_their_counter() -> None:
    app = _app()
    first, _ = _join(app, Role.ADMIN, "first@example.com")
    second, second_id = _join(app, Role.ADMIN, "second@example.com")
    deck_id = _deck(second)
    assert _upload(second, deck_id, 12).status_code == 202  # over the default 10
    assert first.patch(f"/admin/users/{second_id}", json={"role": "member"}).status_code == 200
    resp = _upload(second, deck_id, 1)
    assert resp.status_code == 429
    assert resp.json()["detail"].startswith("You have no AI pages left this month")
