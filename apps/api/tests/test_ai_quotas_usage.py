# SPDX-License-Identifier: AGPL-3.0-only
"""AI quotas, the spend cap and the usage report (F-ADMIN-3, F-ADMIN-4, F-AI-11)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.adapters.memory import (
    InMemoryAiJobRepository,
    InMemoryAuditRepository,
    InMemoryDraftCardRepository,
    InMemoryObjectStore,
    InMemoryQuotaRepository,
    InMemoryQuotaSettingsRepository,
    InMemoryUsageRepository,
    InMemoryUserRepository,
    NullUnitOfWork,
)
from flashcards.api.app import create_app
from flashcards.api.container import Container
from flashcards.application.ai_budget import AiCallBudget
from flashcards.application.ai_spend import AiSpend
from flashcards.application.audit_service import AuditService
from flashcards.application.card_generation_service import CardGenerationService
from flashcards.application.quota_service import QuotaService
from flashcards.config import Settings
from flashcards.domain.ai import StructuredRequest, StructuredResult, Thoroughness, TokenUsage
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import Role
from flashcards.domain.jobs import JobStatus, new_generation_job
from flashcards.domain.quotas import InstanceQuotas, InvalidQuota
from flashcards.domain.sources import ExtractedPage, PageKind, SourceRef
from flashcards.domain.usage import (
    AiFeature,
    AiUsage,
    ProviderUsage,
    SpendCapReached,
    TokenPrices,
    UserUsage,
)
from flashcards.domain.users import User, UserNotFound

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
PASSWORD = "correct-horse-battery"


class _Clock:
    def now(self) -> datetime:
        return NOW


def _result(inp: int = 1000, out: int = 200, model: str = "m1") -> StructuredResult:
    return StructuredResult(data={}, usage=TokenUsage(inp, out), model=model)


def test_cost_is_computed_from_prices_per_million_tokens() -> None:
    prices = TokenPrices(input_usd_per_mtok=3.0, output_usd_per_mtok=15.0)
    assert prices.cost_micro_usd(TokenUsage(1000, 200)) == 6000  # $0.003 + $0.003
    assert TokenPrices().cost_micro_usd(TokenUsage(10**6, 10**6)) == 0


def test_instance_quotas_are_range_checked() -> None:
    with pytest.raises(InvalidQuota):
        InstanceQuotas(default_monthly_pages=-1)
    with pytest.raises(InvalidQuota):
        InstanceQuotas(default_monthly_pages=10**9)
    with pytest.raises(InvalidQuota):
        InstanceQuotas(monthly_spend_cap_micro_usd=-1)


class _Quotas:
    """A QuotaService over in-memory repositories, with handles on its parts."""

    def __init__(self, defaults: InstanceQuotas | None = None) -> None:
        defaults = defaults or InstanceQuotas()
        self.clock = _Clock()
        self.settings = InMemoryQuotaSettingsRepository()
        self.pages = InMemoryQuotaRepository()
        self.usage = InMemoryUsageRepository()
        self.users = InMemoryUserRepository()
        self.events = InMemoryAuditRepository()
        self.service = QuotaService(
            self.settings,
            self.pages,
            self.usage,
            self.users,
            AuditService(self.events, self.clock),
            self.clock,
            defaults=defaults,
        )
        self.spend = AiSpend(
            self.usage,
            self.settings,
            self.clock,
            provider="anthropic",
            prices=TokenPrices(3.0, 15.0),
            defaults=defaults,
        )


async def test_admin_quota_changes_apply_and_are_audited() -> None:
    q = _Quotas()
    await q.users.add(User(id="u1", email="a@example.com", password_hash="x",
                           role=Role.MEMBER, created_at=NOW))
    assert await q.service.instance() == InstanceQuotas()
    assert await q.service.page_limit("u1") == 10

    await q.service.set_instance(
        admin_id="admin", quotas=InstanceQuotas(default_monthly_pages=25,
                                                monthly_spend_cap_micro_usd=5_000_000)
    )
    assert await q.service.page_limit("u1") == 25

    await q.pages.try_reserve_pages("u1", 3, "2026-11")
    quota = await q.service.set_user_pages(admin_id="admin", user_id="u1", pages=100)
    assert (quota.monthly_pages, quota.override, quota.used_pages) == (100, 100, 3)
    assert quota.period == "2026-11"
    assert await q.service.page_limit("u1") == 100

    cleared = await q.service.set_user_pages(admin_id="admin", user_id="u1", pages=None)
    assert (cleared.monthly_pages, cleared.override) == (25, None)

    events = await q.events.list_recent(limit=10)
    assert [(e.action, dict(e.detail)) for e in reversed(events)] == [
        (
            AuditAction.QUOTA_DEFAULTS_CHANGED,
            {"pages_from": "10", "pages_to": "25", "cap_from": "none", "cap_to": "5000000"},
        ),
        (AuditAction.QUOTA_USER_CHANGED, {"from": "none", "to": "100"}),
        (AuditAction.QUOTA_USER_CHANGED, {"from": "100", "to": "none"}),
    ]
    assert all(e.actor_id == "admin" for e in events)


async def test_user_quota_rules() -> None:
    q = _Quotas()
    with pytest.raises(UserNotFound):
        await q.service.user_quota("ghost")
    with pytest.raises(UserNotFound):
        await q.service.set_user_pages(admin_id="admin", user_id="ghost", pages=5)
    with pytest.raises(InvalidQuota):
        await q.service.set_user_pages(admin_id="admin", user_id="ghost", pages=-5)
    assert await q.events.list_recent(limit=10) == []


async def test_the_spend_cap_stops_new_ai_work_once_reached() -> None:
    q = _Quotas()
    await q.spend.ensure_available()  # no cap: always fine
    await q.spend.record("u1", AiFeature.GRADING, _result())  # 6000 micro-USD

    await q.settings.put_instance(InstanceQuotas(monthly_spend_cap_micro_usd=6001))
    await q.spend.ensure_available()
    await q.spend.record("u1", AiFeature.GRADING, _result(inp=1, out=0))  # +3
    with pytest.raises(SpendCapReached):
        await q.spend.ensure_available()

    budget = AiCallBudget(InMemoryQuotaRepository(limit=100), q.clock, q.spend)
    with pytest.raises(SpendCapReached):
        await budget.charge("u2")
    assert await budget._calls.used("u2", "calls:2026-11-08") == 0  # not counted


async def test_a_configured_default_cap_applies_until_an_admin_sets_one() -> None:
    q = _Quotas(defaults=InstanceQuotas(monthly_spend_cap_micro_usd=0))
    with pytest.raises(SpendCapReached):
        await q.spend.ensure_available()
    await q.settings.put_instance(InstanceQuotas(monthly_spend_cap_micro_usd=None))
    await q.spend.ensure_available()


async def test_usage_is_summed_by_user_and_by_provider() -> None:
    q = _Quotas()
    record = await q.spend.record("u1", AiFeature.CHAT, _result())
    assert record == AiUsage(
        id=record.id, owner_id="u1", provider="anthropic", model="m1", feature=AiFeature.CHAT,
        input_tokens=1000, output_tokens=200, cost_micro_usd=6000, period="2026-11",
        created_at=NOW,
    )
    await q.spend.record("u2", AiFeature.SUMMARY, _result(model="m2"))
    await q.spend.record("u2", AiFeature.SUMMARY, _result(model="m2"))
    await q.usage.add(
        AiUsage(id="old", owner_id="u1", provider="anthropic", model="m1",
                feature=AiFeature.CHAT, input_tokens=5, output_tokens=5, cost_micro_usd=99,
                period="2026-10", created_at=NOW)
    )

    report = await q.service.usage_report("2026-11")
    assert report.cost_micro_usd == 18_000 and report.spend_cap_micro_usd is None
    assert report.by_user == [
        UserUsage("u2", 2, 2000, 400, 12_000),
        UserUsage("u1", 1, 1000, 200, 6000),
    ]
    assert report.by_provider == [
        ProviderUsage("anthropic", "m2", 2, 2000, 400, 12_000),
        ProviderUsage("anthropic", "m1", 1, 1000, 200, 6000),
    ]
    assert (await q.service.usage_report("2026-09")).by_user == []


class _Extractor:
    def sniff(self, data: bytes) -> str:  # pragma: no cover - not used by the worker
        return "application/pdf"

    def count_pages(self, data: bytes) -> int:  # pragma: no cover - not used by the worker
        return 1

    async def extract(
        self, data: bytes, *, file_id: str, max_pages: int
    ) -> AsyncIterator[ExtractedPage]:
        yield ExtractedPage(ref=SourceRef(file_id=file_id, page=1), kind=PageKind.TEXT, text="t")


class _UnusableLLM:
    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        return StructuredResult(data={"cards": "nope"}, usage=TokenUsage(500, 50), model="m")


async def test_the_worker_records_usage_even_when_the_output_is_unusable() -> None:
    q = _Quotas()
    jobs = InMemoryAiJobRepository()
    store = InMemoryObjectStore()
    await store.put("src", b"x", "application/pdf")
    job = new_generation_job(owner_id="u1", source_id="src", draft_deck_id="d",
                             thoroughness=Thoroughness.STANDARD, now=NOW)
    await jobs.add(job)
    worker = CardGenerationService(
        jobs, InMemoryDraftCardRepository(), store, _Extractor(), _UnusableLLM(),
        q.clock, NullUnitOfWork(), q.spend,
    )
    await worker.run(job.id)

    failed = await jobs.get(job.id)
    assert failed is not None and failed.status is JobStatus.FAILED
    [usage] = await q.usage.by_user("2026-11")
    assert (usage.owner_id, usage.input_tokens, usage.output_tokens) == ("u1", 500, 50)


# --- HTTP ---


class _GradingLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        self.calls += 1
        return StructuredResult(
            data={"verdict": "correct", "score": 1, "explanation": "ok"},
            usage=TokenUsage(1000, 200),
            model="grader-1",
        )


def _app(llm: _GradingLLM | None = None, **settings: object) -> FastAPI:
    return create_app(
        Settings(
            environment="test",
            session_cookie_secure=False,
            ai_input_usd_per_mtok=3.0,
            ai_output_usd_per_mtok=15.0,
            **settings,  # type: ignore[arg-type]
        ),
        llm=llm or _GradingLLM(),
    )


def _signed_up(app: FastAPI, role: Role, email: str) -> tuple[TestClient, str]:
    container: Container = app.state.container
    token = asyncio.run(container.invites.create(created_by="seed", role=role)).raw_token
    client = TestClient(app)
    response = client.post(
        "/auth/signup", json={"token": token, "email": email, "password": PASSWORD}
    )
    assert response.status_code == 201
    return client, str(response.json()["id"])


def _card(client: TestClient) -> str:
    deck_id = client.post("/decks", json={"name": "Deck"}).json()["id"]
    response = client.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "2+2?", "back": "4"}},
    )
    card_id: str = response.json()[0]["id"]
    return card_id


def _pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"page {i} with enough text to read")
    try:
        return bytes(doc.tobytes())
    finally:
        doc.close()


def _generate(client: TestClient, pages: int) -> int:
    deck_id = client.post("/decks", json={"name": "Gen"}).json()["id"]
    response = client.post(
        "/ai/generate",
        files={"file": ("doc", _pdf(pages), "application/pdf")},
        data={"deck_id": deck_id, "thoroughness": "standard"},
    )
    return response.status_code


def test_admins_read_and_set_quotas_over_http() -> None:
    app = _app(ai_default_monthly_pages=7, ai_monthly_spend_cap_usd=12.5)
    admin, _ = _signed_up(app, Role.ADMIN, "admin@example.com")
    member, member_id = _signed_up(app, Role.MEMBER, "m@example.com")

    assert admin.get("/admin/quotas").json() == {
        "default_monthly_pages": 7,
        "monthly_spend_cap_micro_usd": 12_500_000,
    }
    updated = admin.put(
        "/admin/quotas", json={"default_monthly_pages": 3, "monthly_spend_cap_micro_usd": None}
    )
    assert updated.status_code == 200
    assert admin.get("/admin/quotas").json() == {
        "default_monthly_pages": 3,
        "monthly_spend_cap_micro_usd": None,
    }
    bad = admin.put("/admin/quotas", json={"default_monthly_pages": -1,
                                           "monthly_spend_cap_micro_usd": None})
    assert bad.status_code == 422

    assert admin.get(f"/admin/users/{member_id}/quota").json()["monthly_pages"] == 3
    override = admin.put(f"/admin/users/{member_id}/quota", json={"monthly_pages": 1})
    assert override.status_code == 200
    assert override.json() == {
        "user_id": member_id, "period": override.json()["period"], "monthly_pages": 1,
        "override": 1, "used_pages": 0,
    }
    assert member.get("/account/ai-quota").json()["monthly_pages"] == 1
    # The override limits generation: two pages exceed one.
    assert _generate(member, 2) == 429
    assert _generate(member, 1) == 202
    assert member.get("/account/ai-quota").json()["used_pages"] == 1

    cleared = admin.put(f"/admin/users/{member_id}/quota", json={"monthly_pages": None})
    assert cleared.json()["override"] is None and cleared.json()["monthly_pages"] == 3
    assert admin.get("/admin/users/ghost/quota").status_code == 404
    assert admin.put("/admin/users/ghost/quota", json={"monthly_pages": 2}).status_code == 404

    for path in ("/admin/quotas", f"/admin/users/{member_id}/quota", "/admin/usage"):
        assert member.get(path).status_code == 403
    actions = [e["action"] for e in admin.get("/admin/audit").json()]
    assert actions.count("quota.user_changed") == 2 and "quota.defaults_changed" in actions


def test_ai_calls_are_recorded_and_reported_by_user_and_provider() -> None:
    llm = _GradingLLM()
    app = _app(llm)
    admin, _ = _signed_up(app, Role.ADMIN, "admin@example.com")
    member, member_id = _signed_up(app, Role.MEMBER, "m@example.com")
    card_id = _card(member)
    for _ in range(2):
        assert member.post(f"/cards/{card_id}/evaluate", json={"answer": "4"}).status_code == 200

    report = admin.get("/admin/usage").json()
    assert report["cost_micro_usd"] == 12_000 and report["spend_cap_micro_usd"] is None
    assert report["by_user"] == [{
        "user_id": member_id, "calls": 2, "input_tokens": 2000, "output_tokens": 400,
        "cost_micro_usd": 12_000,
    }]
    assert report["by_provider"] == [{
        "provider": "anthropic", "model": "grader-1", "calls": 2, "input_tokens": 2000,
        "output_tokens": 400, "cost_micro_usd": 12_000,
    }]
    assert admin.get("/admin/usage", params={"period": "2020-01"}).json()["by_user"] == []
    assert admin.get("/admin/usage", params={"period": "2026-13"}).status_code == 422


def test_the_spend_cap_refuses_new_ai_work_over_http() -> None:
    llm = _GradingLLM()
    app = _app(llm)
    admin, _ = _signed_up(app, Role.ADMIN, "admin@example.com")
    member, _ = _signed_up(app, Role.MEMBER, "m@example.com")
    card_id = _card(member)
    assert member.post(f"/cards/{card_id}/evaluate", json={"answer": "4"}).status_code == 200

    admin.put("/admin/quotas",
              json={"default_monthly_pages": 10, "monthly_spend_cap_micro_usd": 6000})
    refused = member.post(f"/cards/{card_id}/evaluate", json={"answer": "4"})
    assert refused.status_code == 429
    assert refused.json()["detail"] == "This instance's monthly AI budget is used up."
    assert llm.calls == 1
    assert _generate(member, 1) == 429
    assert member.get("/account/ai-quota").json()["used_pages"] == 0  # nothing reserved


def test_a_retry_is_refused_once_the_cap_is_reached() -> None:
    app = _app()
    member, _ = _signed_up(app, Role.MEMBER, "m@example.com")
    container: Container = app.state.container
    assert _generate(member, 1) == 202  # queued; no worker runs in this test
    owner = asyncio.run(container.user_repo.get_by_email("m@example.com"))
    assert owner is not None
    asyncio.run(
        container.quotas.set_instance(
            admin_id="a", quotas=InstanceQuotas(monthly_spend_cap_micro_usd=0)
        )
    )
    jobs = asyncio.run(container.generation._jobs.list_for_owner(owner.id))
    with pytest.raises(SpendCapReached):
        asyncio.run(container.generation.retry(owner_id=owner.id, job_id=jobs[0].id))


def test_an_out_of_range_quota_from_the_domain_is_422() -> None:
    # The request models enforce the same bounds, so this guards any other caller.
    app = _app()

    def boom() -> None:
        raise InvalidQuota("monthly pages must be between 0 and 100000")

    app.add_api_route("/test-invalid-quota", boom)
    response = TestClient(app).get("/test-invalid-quota")
    assert response.status_code == 422
    assert response.json()["detail"] == "monthly pages must be between 0 and 100000"
