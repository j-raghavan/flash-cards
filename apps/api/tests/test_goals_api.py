# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP tests for the goal and study-plan routes (in-memory mode)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.domain.invites import Role

PASSWORD = "correct-horse-battery"
FUTURE = "2030-01-01T00:00:00Z"


@pytest.fixture
def member(app: FastAPI, seed_invite: Callable[..., str]) -> TestClient:
    client = TestClient(app)
    token = seed_invite(role=Role.MEMBER)
    resp = client.post(
        "/auth/signup", json={"token": token, "email": "m@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 201
    return client


def _deck(client: TestClient, name: str = "Bio") -> str:
    deck_id: str = client.post("/decks", json={"name": name}).json()["id"]
    return deck_id


def test_goal_lifecycle(member: TestClient) -> None:
    deck_id = _deck(member)
    created = member.post(
        "/goals",
        json={"name": "Final", "deck_ids": [deck_id], "exam_date": FUTURE, "desired_grade": 0.5},
    )
    assert created.status_code == 201
    goal = created.json()
    assert goal["deck_ids"] == [deck_id]
    assert 0.80 <= goal["target_retention"] <= 0.97
    goal_id = goal["id"]

    assert [g["id"] for g in member.get("/goals").json()] == [goal_id]
    assert member.get(f"/goals/{goal_id}").json()["name"] == "Final"
    assert member.get("/goals/missing").status_code == 404

    patched = member.patch(f"/goals/{goal_id}", json={"name": "Retake", "desired_grade": 1.0})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Retake"
    assert patched.json()["target_retention"] == 0.97

    assert member.delete(f"/goals/{goal_id}").status_code == 204
    assert member.get(f"/goals/{goal_id}").status_code == 404


def test_create_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/goals", json={"name": "x", "deck_ids": ["d"], "exam_date": FUTURE, "desired_grade": 0.5}
    )
    assert resp.status_code == 401


def test_create_on_a_foreign_deck_is_not_found(member: TestClient) -> None:
    resp = member.post(
        "/goals",
        json={"name": "x", "deck_ids": ["nope"], "exam_date": FUTURE, "desired_grade": 0.5},
    )
    assert resp.status_code == 404


def test_create_rejects_bad_input(member: TestClient) -> None:
    deck_id = _deck(member)
    # No decks -> schema rejects.
    assert (
        member.post(
            "/goals",
            json={"name": "x", "deck_ids": [], "exam_date": FUTURE, "desired_grade": 0.5},
        ).status_code
        == 422
    )
    # Exam date in the past -> domain rejects.
    assert (
        member.post(
            "/goals",
            json={
                "name": "x",
                "deck_ids": [deck_id],
                "exam_date": "2000-01-01T00:00:00Z",
                "desired_grade": 0.5,
            },
        ).status_code
        == 422
    )


def test_plan_reflects_the_decks_cards(member: TestClient) -> None:
    deck_id = _deck(member)
    member.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "q", "back": "a"}},
    )
    goal_id = member.post(
        "/goals",
        json={"name": "Final", "deck_ids": [deck_id], "exam_date": FUTURE, "desired_grade": 0.9},
    ).json()["id"]

    plan = member.get(f"/goals/{goal_id}/plan")
    assert plan.status_code == 200
    body = plan.json()
    assert body["total_cards"] == 1
    assert body["new_cards"] == 1
    assert body["days_until_exam"] > 0
    assert 0.0 <= body["readiness"] <= 1.0
    assert member.get("/goals/missing/plan").status_code == 404
