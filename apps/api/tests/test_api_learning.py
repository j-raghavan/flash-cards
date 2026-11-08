# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

PASSWORD = "correct-horse-battery"


def _second_user(app: FastAPI, admin_client: TestClient) -> TestClient:
    token = admin_client.post("/admin/invites", json={"role": "member"}).json()["token"]
    bob = TestClient(app)
    bob.post(
        "/auth/signup",
        json={"token": token, "email": "bob@example.com", "password": PASSWORD},
    )
    return bob


def _make_deck(client: TestClient, name: str = "Biology", parent: str | None = None) -> str:
    response = client.post("/decks", json={"name": name, "parent_id": parent})
    assert response.status_code == 201
    return str(response.json()["id"])


def test_deck_crud_flow(admin_client: TestClient) -> None:
    deck_id = _make_deck(admin_client)
    assert any(d["id"] == deck_id for d in admin_client.get("/decks").json())
    assert admin_client.get(f"/decks/{deck_id}").status_code == 200

    renamed = admin_client.patch(f"/decks/{deck_id}", json={"name": "Chemistry"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Chemistry"

    assert admin_client.delete(f"/decks/{deck_id}").status_code == 204
    assert admin_client.get(f"/decks/{deck_id}").status_code == 404


def test_learning_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/decks").status_code == 401
    assert client.get("/decks/x/cards").status_code == 401
    assert client.get("/decks/x/stats").status_code == 401
    assert client.get("/decks/x/review").status_code == 401
    assert client.post("/cards/x/review", json={"rating": 3}).status_code == 401


def test_blank_deck_name_is_422(admin_client: TestClient) -> None:
    assert admin_client.post("/decks", json={"name": "   "}).status_code == 422


def test_move_cycle_is_409(admin_client: TestClient) -> None:
    a = _make_deck(admin_client, "A")
    b = _make_deck(admin_client, "B", parent=a)
    moved = admin_client.post(f"/decks/{a}/move", json={"parent_id": b})
    assert moved.status_code == 409


def test_move_deck_succeeds(admin_client: TestClient) -> None:
    a = _make_deck(admin_client, "A")
    b = _make_deck(admin_client, "B")
    moved = admin_client.post(f"/decks/{b}/move", json={"parent_id": a})
    assert moved.status_code == 200
    assert moved.json()["parent_id"] == a


def test_create_basic_and_cloze_notes(admin_client: TestClient) -> None:
    deck_id = _make_deck(admin_client)
    basic = admin_client.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "Q", "back": "A"}},
    )
    assert basic.status_code == 201
    assert len(basic.json()) == 1

    cloze = admin_client.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "cloze", "fields": {"text": "{{c1::a}} {{c2::b}}"}},
    )
    assert cloze.status_code == 201
    assert len(cloze.json()) == 2


def test_invalid_note_is_422(admin_client: TestClient) -> None:
    deck_id = _make_deck(admin_client)
    response = admin_client.post(
        f"/decks/{deck_id}/notes", json={"note_type": "basic", "fields": {"front": "Q"}}
    )
    assert response.status_code == 422


def test_card_detail_suspend_and_delete_note(admin_client: TestClient) -> None:
    deck_id = _make_deck(admin_client)
    cards = admin_client.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "Q", "back": "A"}},
    ).json()
    card_id = cards[0]["id"]
    note_id = cards[0]["note_id"]

    detail = admin_client.get(f"/cards/{card_id}")
    assert detail.status_code == 200
    assert detail.json()["fields"] == {"front": "Q", "back": "A"}

    assert admin_client.post(f"/cards/{card_id}/suspend").json()["suspended"] is True
    assert admin_client.post(f"/cards/{card_id}/unsuspend").json()["suspended"] is False

    assert admin_client.delete(f"/notes/{note_id}").status_code == 204
    assert admin_client.get(f"/decks/{deck_id}/cards").json() == []


def test_review_queue_submit_and_stats(admin_client: TestClient) -> None:
    deck_id = _make_deck(admin_client)
    card_id = admin_client.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "Q", "back": "A"}},
    ).json()[0]["id"]

    queue = admin_client.get(f"/decks/{deck_id}/review")
    assert [c["id"] for c in queue.json()] == [card_id]

    graded = admin_client.post(f"/cards/{card_id}/review", json={"rating": 3})
    assert graded.status_code == 200
    assert graded.json()["card_id"] == card_id

    # Now scheduled out, so no longer in today's queue.
    assert admin_client.get(f"/decks/{deck_id}/review").json() == []

    stats = admin_client.get(f"/decks/{deck_id}/stats").json()
    assert stats["total"] == 1
    assert stats["suspended"] == 0


def test_review_is_idempotent_on_key(admin_client: TestClient) -> None:
    deck_id = _make_deck(admin_client)
    card_id = admin_client.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "Q", "back": "A"}},
    ).json()[0]["id"]
    first = admin_client.post(
        f"/cards/{card_id}/review", json={"rating": 3, "idempotency_key": "k1"}
    ).json()
    second = admin_client.post(
        f"/cards/{card_id}/review", json={"rating": 1, "idempotency_key": "k1"}
    ).json()
    assert second["due_at"] == first["due_at"]


def test_delete_deck_cascades_its_cards(admin_client: TestClient) -> None:
    deck_id = _make_deck(admin_client)
    card_id = admin_client.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "Q", "back": "A"}},
    ).json()[0]["id"]
    assert admin_client.delete(f"/decks/{deck_id}").status_code == 204
    # The card is gone, not merely unreachable: a consistent 404 in both backends.
    assert admin_client.get(f"/cards/{card_id}").status_code == 404


def test_invalid_rating_is_422(admin_client: TestClient) -> None:
    deck_id = _make_deck(admin_client)
    card_id = admin_client.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "Q", "back": "A"}},
    ).json()[0]["id"]
    assert admin_client.post(f"/cards/{card_id}/review", json={"rating": 9}).status_code == 422


def test_foreign_deck_and_card_are_404(app: FastAPI, admin_client: TestClient) -> None:
    bob = _second_user(app, admin_client)
    bob_deck = _make_deck(bob, "Bob's")
    bob_card = bob.post(
        f"/decks/{bob_deck}/notes",
        json={"note_type": "basic", "fields": {"front": "Q", "back": "A"}},
    ).json()[0]["id"]

    assert admin_client.get(f"/decks/{bob_deck}").status_code == 404
    assert admin_client.get(f"/decks/{bob_deck}/cards").status_code == 404
    assert admin_client.get(f"/cards/{bob_card}").status_code == 404
    assert admin_client.post(f"/cards/{bob_card}/review", json={"rating": 3}).status_code == 404
