# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP tests for the shared admin library (ADR 0024, in-memory mode)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.domain.invites import Role

PASSWORD = "correct-horse-battery"

VOCAB = {
    "name": "Vocabulary",
    "fields": ["Word", "Meaning"],
    "templates": [{"name": "Forward", "front": "{{Word}}", "back": "{{Meaning}}"}],
}
MASKS = [{"id": 1, "left": 0.1, "top": 0.1, "width": 0.2, "height": 0.1}]


def _signup(app: FastAPI, seed_invite: Callable[..., str], email: str, role: Role) -> TestClient:
    client = TestClient(app)
    resp = client.post(
        "/auth/signup",
        json={"token": seed_invite(role=role), "email": email, "password": PASSWORD},
    )
    assert resp.status_code == 201
    return client


def _png() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=32, height=32)
    try:
        return bytes(page.get_pixmap().tobytes("png"))
    finally:
        doc.close()


@dataclass
class Library:
    """An admin's deck with a basic, a custom and an image occlusion note."""

    admin: TestClient
    member: TestClient
    deck_id: str
    basic_card: str
    basic_note: str
    custom_card: str
    note_type_id: str
    occlusion_card: str
    image_id: str


@pytest.fixture
def member(app: FastAPI, seed_invite: Callable[..., str]) -> TestClient:
    return _signup(app, seed_invite, "member@example.com", Role.MEMBER)


@pytest.fixture
def library(admin_client: TestClient, member: TestClient) -> Library:
    admin = admin_client
    deck_id = admin.post("/decks", json={"name": "Biology"}).json()["id"]
    [basic] = admin.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "Q", "back": "A"}},
    ).json()
    note_type_id = admin.post("/note-types", json=VOCAB).json()["id"]
    [custom] = admin.post(
        f"/decks/{deck_id}/notes",
        json={
            "note_type": "custom",
            "note_type_id": note_type_id,
            "fields": {"Word": "cell", "Meaning": "unit of life"},
        },
    ).json()
    image_id = admin.post("/media", files={"file": ("x.png", _png(), "image/png")}).json()["id"]
    [occlusion] = admin.post(
        f"/decks/{deck_id}/notes",
        json={
            "note_type": "image_occlusion",
            "fields": {"header": "Heart"},
            "occlusion": {"image_id": image_id, "masks": MASKS},
        },
    ).json()
    return Library(
        admin=admin,
        member=member,
        deck_id=deck_id,
        basic_card=basic["id"],
        basic_note=basic["note_id"],
        custom_card=custom["id"],
        note_type_id=note_type_id,
        occlusion_card=occlusion["id"],
        image_id=image_id,
    )


def test_member_sees_library_decks_next_to_their_own(library: Library) -> None:
    own = library.member.post("/decks", json={"name": "Mine"}).json()
    assert own["owned"] is True

    decks = {d["id"]: d for d in library.member.get("/decks").json()}

    assert decks[library.deck_id]["owned"] is False and decks[own["id"]]["owned"] is True
    assert [d["owned"] for d in library.admin.get("/decks").json()] == [True]
    one = library.member.get(f"/decks/{library.deck_id}")
    assert one.status_code == 200 and one.json()["owned"] is False
    assert library.admin.get(f"/decks/{library.deck_id}").json()["owned"] is True


def test_member_reads_and_renders_library_cards(library: Library) -> None:
    member = library.member
    cards = member.get(f"/decks/{library.deck_id}/cards")
    assert cards.status_code == 200
    assert {c["id"] for c in cards.json()} == {
        library.basic_card,
        library.custom_card,
        library.occlusion_card,
    }

    basic = member.get(f"/cards/{library.basic_card}").json()
    assert basic["fields"] == {"front": "Q", "back": "A"}
    custom = member.get(f"/cards/{library.custom_card}").json()
    assert custom["template"]["front"] == "{{Word}}"
    note = member.get(f"/notes/{library.basic_note}")
    assert note.status_code == 200 and [c["id"] for c in note.json()["cards"]] == [
        library.basic_card
    ]
    note_type = member.get(f"/note-types/{library.note_type_id}")
    assert note_type.status_code == 200 and note_type.json()["name"] == "Vocabulary"
    occlusion = member.get(f"/cards/{library.occlusion_card}").json()
    assert occlusion["occlusion"]["image_id"] == library.image_id
    image = member.get(f"/media/{library.image_id}")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    # The admin's own list of note types stays the admin's.
    assert member.get("/note-types").json() == []


def test_bury_and_flag_are_the_owners_own_markers(library: Library) -> None:
    admin, member, card = library.admin, library.member, library.basic_card
    for body in ({"action": "flag", "flag": 3}, {"action": "bury"}):
        assert admin.post("/cards/bulk", json={"card_ids": [card], **body}).status_code == 200

    mine = admin.get(f"/cards/{card}").json()
    assert mine["flag"] == 3 and mine["buried_until"] is not None
    seen = member.get(f"/cards/{card}").json()
    assert (seen["flag"], seen["buried_until"]) == (0, None)
    listed = {c["id"]: c for c in member.get(f"/decks/{library.deck_id}/cards").json()}
    assert (listed[card]["flag"], listed[card]["buried_until"]) == (0, None)
    [in_note] = member.get(f"/notes/{library.basic_note}").json()["cards"]
    assert (in_note["flag"], in_note["buried_until"]) == (0, None)


def test_suspension_is_shown_to_everyone(library: Library) -> None:
    library.admin.post(f"/cards/{library.basic_card}/suspend")

    assert library.member.get(f"/cards/{library.basic_card}").json()["suspended"] is True


# Every write a member might try on library material: (method, path, json body).
WRITES: list[tuple[str, str, dict[str, object] | None]] = [
    ("PATCH", "/decks/{deck}", {"name": "Renamed"}),
    ("DELETE", "/decks/{deck}", None),
    ("POST", "/decks/{deck}/move", {"parent_id": None}),
    ("GET", "/decks/{deck}/export", None),
    ("POST", "/decks/{deck}/shares", {"allow_clone": True}),
    ("GET", "/decks/{deck}/shares", None),
    ("POST", "/decks/{deck}/notes", {"note_type": "basic", "fields": {"front": "x", "back": "y"}}),
    ("PATCH", "/notes/{note}", {"fields": {"front": "edited", "back": "A"}}),
    ("DELETE", "/notes/{note}", None),
    ("POST", "/cards/{card}/suspend", None),
    ("POST", "/cards/{card}/unsuspend", None),
    ("PATCH", "/note-types/{note_type}", {"name": "Mine now"}),
    ("DELETE", "/note-types/{note_type}", None),
    ("POST", "/decks/{deck}/summary", None),
    ("POST", "/decks/{deck}/chat", {"question": "What is a cell?"}),
    ("POST", "/decks/{deck}/mock-exam", {}),
    ("POST", "/cards/{card}/evaluate", {"answer": "A"}),
]


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_library_material_is_read_only_to_members(
    library: Library, method: str, path: str, body: dict[str, object] | None
) -> None:
    url = path.format(
        deck=library.deck_id,
        note=library.basic_note,
        card=library.basic_card,
        note_type=library.note_type_id,
    )

    resp = library.member.request(method, url, json=body)

    assert resp.status_code == 404, resp.text
    # Nothing changed for the admin.
    assert library.admin.get(f"/decks/{library.deck_id}").json()["name"] == "Biology"
    detail = library.admin.get(f"/cards/{library.basic_card}").json()
    assert detail["fields"]["front"] == "Q" and detail["suspended"] is False


def test_members_cannot_place_their_decks_inside_a_library_deck(library: Library) -> None:
    member = library.member
    child = member.post("/decks", json={"name": "Child", "parent_id": library.deck_id})
    assert child.status_code == 404
    own = member.post("/decks", json={"name": "Mine"}).json()
    moved = member.post(f"/decks/{own['id']}/move", json={"parent_id": library.deck_id})
    assert moved.status_code == 404


def test_another_members_decks_stay_private(
    library: Library, app: FastAPI, seed_invite: Callable[..., str]
) -> None:
    other = _signup(app, seed_invite, "other@example.com", Role.MEMBER)
    private = other.post("/decks", json={"name": "Private"}).json()["id"]
    [card] = other.post(
        f"/decks/{private}/notes",
        json={"note_type": "basic", "fields": {"front": "x", "back": "y"}},
    ).json()

    assert private not in {d["id"] for d in library.member.get("/decks").json()}
    assert private not in {d["id"] for d in library.admin.get("/decks").json()}
    assert library.member.get(f"/decks/{private}").status_code == 404
    assert library.member.get(f"/decks/{private}/cards").status_code == 404
    assert library.member.get(f"/cards/{card['id']}").status_code == 404
    assert library.admin.get(f"/notes/{card['note_id']}").status_code == 404


def test_a_second_admin_reads_the_first_admins_decks_read_only(
    library: Library, app: FastAPI, seed_invite: Callable[..., str]
) -> None:
    second = _signup(app, seed_invite, "second-admin@example.com", Role.ADMIN)

    decks = {d["id"]: d for d in second.get("/decks").json()}

    assert decks[library.deck_id]["owned"] is False
    assert second.get(f"/cards/{library.custom_card}").status_code == 200
    assert second.patch(f"/decks/{library.deck_id}", json={"name": "x"}).status_code == 404
    assert second.delete(f"/notes/{library.basic_note}").status_code == 404


def test_a_member_studies_the_library_end_to_end(
    admin_client: TestClient, app: FastAPI, seed_invite: Callable[..., str]
) -> None:
    admin = admin_client
    deck_id = admin.post("/decks", json={"name": "Spanish"}).json()["id"]
    [card] = admin.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "hola", "back": "hello"}},
    ).json()
    member = _signup(app, seed_invite, "learner@example.com", Role.MEMBER)

    assert deck_id in {d["id"] for d in member.get("/decks").json() if not d["owned"]}
    assert [c["id"] for c in member.get(f"/decks/{deck_id}/review").json()] == [card["id"]]
    assert member.get(f"/cards/{card['id']}").json()["fields"]["front"] == "hola"
    answered = member.post(f"/cards/{card['id']}/review", json={"rating": 3})
    assert answered.status_code == 200

    mine = member.get(f"/decks/{deck_id}/stats").json()
    assert mine["new"] == 0 and mine["learning"] + mine["review"] == 1
    progress = member.get("/progress/stats", params={"deck_id": deck_id, "days": 1}).json()
    assert progress["heatmap"][-1]["reviews"] == 1
    assert admin.get(f"/decks/{deck_id}/stats").json()["new"] == 1  # the admin's own
    assert [c["id"] for c in admin.get(f"/decks/{deck_id}/review").json()] == [card["id"]]

    # A second admin demotes the first: the deck leaves the library at once.
    second = _signup(app, seed_invite, "second@example.com", Role.ADMIN)
    first_id = admin.get("/auth/me").json()["id"]
    assert second.patch(f"/admin/users/{first_id}", json={"role": "member"}).status_code == 200

    assert deck_id not in {d["id"] for d in member.get("/decks").json()}
    assert member.get(f"/decks/{deck_id}").status_code == 404
    assert member.get(f"/decks/{deck_id}/review").status_code == 404
    assert member.get(f"/decks/{deck_id}/stats").status_code == 404
    assert member.get(f"/cards/{card['id']}").status_code == 404
    late = member.post(f"/cards/{card['id']}/review", json={"rating": 3, "idempotency_key": "k"})
    assert late.status_code == 404  # an offline answer replayed after access is gone
    # The former admin keeps studying their own deck.
    assert admin.get(f"/decks/{deck_id}").json()["owned"] is True
