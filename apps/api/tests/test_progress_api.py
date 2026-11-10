# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP surface for study settings, the daily goal and statistics (F-PROG-1, F-PROG-3)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

PASSWORD = "correct-horse-battery"


def _deck_with_cards(client: TestClient, count: int, name: str = "Bio") -> tuple[str, list[str]]:
    deck_id = str(client.post("/decks", json={"name": name}).json()["id"])
    card_ids = []
    for n in range(count):
        created = client.post(
            f"/decks/{deck_id}/notes",
            json={"note_type": "basic", "fields": {"front": f"Q{n}", "back": "A"}},
        )
        card_ids.append(str(created.json()[0]["id"]))
    return deck_id, card_ids


def _second_user(
    app: FastAPI, admin_client: TestClient, email: str = "bob@example.com"
) -> TestClient:
    token = admin_client.post("/admin/invites", json={"role": "member"}).json()["token"]
    bob = TestClient(app)
    bob.post("/auth/signup", json={"token": token, "email": email, "password": PASSWORD})
    return bob


def test_progress_routes_require_auth(client: TestClient) -> None:
    assert client.get("/progress/settings").status_code == 401
    assert client.patch("/progress/settings", json={}).status_code == 401
    assert client.get("/progress/today").status_code == 401
    assert client.get("/progress/stats").status_code == 401


def test_settings_round_trip_and_validation(admin_client: TestClient) -> None:
    assert admin_client.get("/progress/settings").json() == {"daily_goal": 20, "time_zone": "UTC"}
    updated = admin_client.patch(
        "/progress/settings", json={"daily_goal": 2, "time_zone": "America/New_York"}
    )
    assert updated.json() == {"daily_goal": 2, "time_zone": "America/New_York"}
    unknown = admin_client.patch("/progress/settings", json={"time_zone": "Mars/Base"})
    assert unknown.status_code == 422
    assert admin_client.patch("/progress/settings", json={"daily_goal": 0}).status_code == 422
    too_long = admin_client.patch("/progress/settings", json={"time_zone": "x" * 65})
    assert too_long.status_code == 422
    assert admin_client.get("/progress/settings").json()["time_zone"] == "America/New_York"
    exported = admin_client.get("/account/export").json()
    assert exported["study_settings"] == {"daily_goal": 2, "time_zone": "America/New_York"}


def test_answers_report_the_daily_goal_and_celebrate_once(admin_client: TestClient) -> None:
    admin_client.patch("/progress/settings", json={"daily_goal": 2})
    _deck, (a, b, c) = _deck_with_cards(admin_client, 3)

    first = admin_client.post(f"/cards/{a}/review", json={"rating": 3}).json()
    assert first["card_id"] == a
    assert first["daily_goal"]["reviews"] == 1 and first["daily_goal"]["goal_met"] is False
    second = admin_client.post(f"/cards/{b}/review", json={"rating": 3}).json()
    assert second["daily_goal"]["just_completed"] is True
    third = admin_client.post(f"/cards/{c}/review", json={"rating": 1}).json()
    assert third["daily_goal"]["goal_met"] is True
    assert third["daily_goal"]["just_completed"] is False

    today = admin_client.get("/progress/today").json()
    assert today["reviews"] == 3 and today["streak"] == 1 and today["time_zone"] == "UTC"


def test_stats_overall_and_per_deck(app: FastAPI, admin_client: TestClient) -> None:
    deck, (a, _b) = _deck_with_cards(admin_client, 2)
    _other, (c,) = _deck_with_cards(admin_client, 1, "Chem")
    admin_client.post(f"/cards/{a}/review", json={"rating": 3})
    admin_client.post(f"/cards/{c}/review", json={"rating": 1})

    overall = admin_client.get("/progress/stats", params={"days": 7, "forecast_days": 400})
    assert overall.status_code == 422  # forecast range is capped
    overall = admin_client.get("/progress/stats", params={"days": 7, "forecast_days": 30})
    body = overall.json()
    assert len(body["heatmap"]) == 7 and body["heatmap"][-1]["reviews"] == 2
    assert body["retention"][-1]["reviews"] == 2 and body["retention"][-1]["rate"] == 0.5
    assert len(body["forecast"]) == 30
    assert sum(day["due"] for day in body["forecast"]) == 2

    per_deck = admin_client.get("/progress/stats", params={"deck_id": deck}).json()
    assert per_deck["deck_id"] == deck and len(per_deck["heatmap"]) == 90
    assert per_deck["heatmap"][-1]["reviews"] == 1
    assert admin_client.get("/progress/stats", params={"days": 367}).status_code == 422
    # The admin's deck is a library deck (ADR 0024): a member reads their own
    # progress on it, none so far, but another member's deck stays private.
    bob = _second_user(app, admin_client)
    library = bob.get("/progress/stats", params={"deck_id": deck})
    assert library.status_code == 200
    assert sum(day["reviews"] for day in library.json()["heatmap"]) == 0
    assert sum(day["due"] for day in library.json()["forecast"]) == 0
    bobs_deck, _cards = _deck_with_cards(bob, 1, "Bob's")
    carol = _second_user(app, admin_client, "carol@example.com")
    assert carol.get("/progress/stats", params={"deck_id": bobs_deck}).status_code == 404
