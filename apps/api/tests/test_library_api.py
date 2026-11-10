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


def test_a_suspended_library_card_is_hidden_from_members(library: Library) -> None:
    admin, member, card = library.admin, library.member, library.basic_card
    admin.post(f"/cards/{card}/suspend")

    assert member.get(f"/cards/{card}").status_code == 404
    assert card not in {c["id"] for c in member.get(f"/decks/{library.deck_id}/cards").json()}
    assert card not in {c["id"] for c in member.get(f"/decks/{library.deck_id}/review").json()}
    assert member.get(f"/notes/{library.basic_note}").json()["cards"] == []
    assert member.post(f"/cards/{card}/review", json={"rating": 3}).status_code == 404
    # The admin still sees it, suspended.
    assert admin.get(f"/cards/{card}").json()["suspended"] is True
    assert admin.post(f"/cards/{card}/unsuspend").status_code == 200
    assert member.get(f"/cards/{card}").status_code == 200


def test_card_and_note_payloads_say_who_owns_them(library: Library) -> None:
    card, note = library.basic_card, library.basic_note

    assert library.member.get(f"/cards/{card}").json()["owned"] is False
    assert library.member.get(f"/notes/{note}").json()["owned"] is False
    assert library.admin.get(f"/cards/{card}").json()["owned"] is True
    assert library.admin.get(f"/notes/{note}").json()["owned"] is True
    edited = library.admin.patch(f"/notes/{note}", json={"tags": ["bio"]})
    assert edited.status_code == 200 and edited.json()["owned"] is True


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


def _admin_view(library: Library) -> list[object]:
    """Everything the admin sees of their library material."""
    admin = library.admin
    return [
        admin.get(f"/decks/{library.deck_id}").json(),
        admin.get(f"/decks/{library.deck_id}/cards").json(),
        admin.get(f"/decks/{library.deck_id}/stats").json(),
        admin.get(f"/cards/{library.basic_card}").json(),
        admin.get(f"/notes/{library.basic_note}").json(),
        admin.get("/goals").json(),
    ]


# Every bulk action, as (action, extra body); a move names its target separately.
BULK: list[tuple[str, dict[str, object]]] = [
    ("suspend", {}),
    ("unsuspend", {}),
    ("bury", {}),
    ("unbury", {}),
    ("flag", {"flag": 2}),
    ("delete", {}),
    ("add_tags", {"tags": ["x"]}),
    ("remove_tags", {"tags": ["x"]}),
    ("move", {}),
]


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize(("action", "extra"), BULK)
def test_bulk_changes_to_library_cards_fail_closed(
    library: Library, action: str, extra: dict[str, object], mixed: bool
) -> None:
    member = library.member
    own_deck = member.post("/decks", json={"name": "Mine"}).json()["id"]
    [own] = member.post(
        f"/decks/{own_deck}/notes",
        json={"note_type": "basic", "fields": {"front": "x", "back": "y"}},
    ).json()
    before = _admin_view(library)
    ids = [own["id"], library.basic_card] if mixed else [library.basic_card]
    body: dict[str, object] = {"card_ids": ids, "action": action, **extra}
    if action == "move":
        body["deck_id"] = own_deck  # a library card into the member's own deck

    assert member.post("/cards/bulk", json=body).status_code == 404
    if action == "move":  # and the member's own card into the library deck
        into = {"card_ids": [own["id"]], "action": "move", "deck_id": library.deck_id}
        assert member.post("/cards/bulk", json=into).status_code == 404
    assert _admin_view(library) == before
    assert member.get(f"/cards/{own['id']}").json()["deck_id"] == own_deck  # all or nothing


def test_owner_only_features_refuse_library_decks(library: Library) -> None:
    member, deck = library.member, library.deck_id
    goal = {"name": "Exam", "exam_date": "2027-01-01T00:00:00Z", "desired_grade": 0.8}
    own_deck = member.post("/decks", json={"name": "Mine"}).json()["id"]
    own_goal = member.post("/goals", json={**goal, "deck_ids": [own_deck]}).json()["id"]
    job = library.admin.post(
        "/ai/generate/transcript", json={"deck_id": deck, "transcript": "Cells. " * 50}
    )
    assert job.status_code == 202  # the admin's own job on the library deck
    before = _admin_view(library)

    assert member.get("/cards", params={"deck_id": deck}).status_code == 404
    assert member.post("/goals", json={**goal, "deck_ids": [deck]}).status_code == 404
    assert member.patch(f"/goals/{own_goal}", json={"deck_ids": [deck]}).status_code == 404
    transcript = {"deck_id": deck, "transcript": "Cells. " * 50}
    assert member.post("/ai/generate/transcript", json=transcript).status_code == 404
    upload = member.post(
        "/ai/generate",
        data={"deck_id": deck},
        files={"file": ("notes.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert upload.status_code == 404
    approve = member.post(f"/ai/jobs/{job.json()['id']}/approve", json={"draft_ids": ["d"]})
    assert approve.status_code == 404
    assert _admin_view(library) == before


def test_a_library_sub_deck_keeps_its_parent(library: Library) -> None:
    child = library.admin.post(
        "/decks", json={"name": "Cells", "parent_id": library.deck_id}
    ).json()["id"]

    decks = {d["id"]: d for d in library.member.get("/decks").json()}

    assert decks[child]["parent_id"] == library.deck_id and decks[child]["owned"] is False
