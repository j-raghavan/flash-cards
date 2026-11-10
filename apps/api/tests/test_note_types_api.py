# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP tests for custom note types and image occlusion notes (in-memory mode)."""

from __future__ import annotations

import asyncio

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flashcards.api.app import create_app
from flashcards.api.container import Container
from flashcards.application import sharing_service
from flashcards.config import Settings
from flashcards.domain.ai import StructuredRequest, StructuredResult, TokenUsage
from flashcards.domain.invites import Role
from flashcards.domain.notes import MAX_MASKS

PASSWORD = "correct-horse-battery"
VOCAB = {
    "name": "Vocabulary",
    "fields": ["Word", "Meaning"],
    "templates": [
        {"name": "Forward", "front": "{{Word}}", "back": "{{FrontSide}}<hr>{{Meaning}}"},
        {"name": "Reverse", "front": "{{Meaning}}", "back": "{{FrontSide}}<hr>{{Word}}"},
    ],
}
MASKS = [
    {"id": 1, "left": 0.1, "top": 0.1, "width": 0.2, "height": 0.1},
    {"id": 2, "left": 0.5, "top": 0.5, "width": 0.2, "height": 0.1},
]


class _FakeLLM:
    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        data: dict[str, object] = {"verdict": "correct", "score": 1.0, "explanation": "ok"}
        return StructuredResult(data=data, usage=TokenUsage(1, 1), model="fake")


@pytest.fixture
def app() -> FastAPI:
    return create_app(Settings(environment="test", session_cookie_secure=False), llm=_FakeLLM())


def _member(app: FastAPI, email: str) -> TestClient:
    container: Container = app.state.container
    token = asyncio.run(container.invites.create(created_by="seed", role=Role.MEMBER)).raw_token
    client = TestClient(app)
    resp = client.post(
        "/auth/signup", json={"token": token, "email": email, "password": PASSWORD}
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


def _image(client: TestClient) -> str:
    resp = client.post("/media", files={"file": ("x.png", _png(), "image/png")})
    assert resp.status_code == 201
    image_id: str = resp.json()["id"]
    return image_id


def _deck(client: TestClient, name: str = "Lang") -> str:
    deck_id: str = client.post("/decks", json={"name": name}).json()["id"]
    return deck_id


@pytest.fixture
def alice(app: FastAPI) -> TestClient:
    return _member(app, "alice@example.com")


def test_note_type_crud(alice: TestClient, app: FastAPI) -> None:
    created = alice.post("/note-types", json=VOCAB)
    assert created.status_code == 201
    body = created.json()
    type_id = body["id"]
    assert body["fields"] == ["Word", "Meaning"]
    named = [(t["ordinal"], t["name"]) for t in body["templates"]]
    assert named == [(0, "Forward"), (1, "Reverse")]

    assert [t["id"] for t in alice.get("/note-types").json()] == [type_id]
    assert alice.get(f"/note-types/{type_id}").json() == body

    renamed = alice.patch(f"/note-types/{type_id}", json={"name": "Words"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Words"

    bob = _member(app, "bob@example.com")
    assert bob.get(f"/note-types/{type_id}").status_code == 404
    assert bob.patch(f"/note-types/{type_id}", json={"name": "x"}).status_code == 404
    assert bob.delete(f"/note-types/{type_id}").status_code == 404
    assert bob.get("/note-types").json() == []

    assert alice.delete(f"/note-types/{type_id}").status_code == 204
    assert alice.get(f"/note-types/{type_id}").status_code == 404


def test_note_type_validation_errors(alice: TestClient) -> None:
    bad_ref = {**VOCAB, "templates": [{"name": "C", "front": "{{Nope}}", "back": ""}]}
    resp = alice.post("/note-types", json=bad_ref)
    assert resp.status_code == 422
    assert resp.json()["detail"] == "template 1: the front uses a placeholder that is not a field"
    huge = {**VOCAB, "templates": [{"name": "C", "front": "x" * 10_001, "back": ""}]}
    assert alice.post("/note-types", json=huge).status_code == 422
    assert alice.post("/note-types", json={**VOCAB, "fields": ["x" * 65]}).status_code == 422
    assert alice.get("/note-types").json() == []


def test_custom_notes_render_from_their_template(alice: TestClient) -> None:
    type_id = alice.post("/note-types", json=VOCAB).json()["id"]
    deck_id = _deck(alice)
    resp = alice.post(
        f"/decks/{deck_id}/notes",
        json={
            "note_type": "custom",
            "note_type_id": type_id,
            "fields": {"Word": "chat", "Meaning": "cat"},
        },
    )
    assert resp.status_code == 201
    cards = resp.json()
    assert [c["ordinal"] for c in cards] == [0, 1]

    detail = alice.get(f"/cards/{cards[1]['id']}").json()
    assert detail["note_type"] == "custom" and detail["note_type_id"] == type_id
    assert detail["template"] == {
        "ordinal": 1, "name": "Reverse", "front": "{{Meaning}}",
        "back": "{{FrontSide}}<hr>{{Word}}",
    }
    assert detail["occlusion"] is None

    note_id = detail["note_id"]
    note = alice.get(f"/notes/{note_id}").json()
    assert note["note_type_id"] == type_id and note["occlusion"] is None

    # Dropping Reverse takes its card; Forward's card survives.
    edited = alice.patch(
        f"/note-types/{type_id}",
        json={"templates": [{"ordinal": 0, "name": "Forward", "front": "{{Word}}", "back": "x"}]},
    )
    assert edited.status_code == 200
    after = alice.get(f"/notes/{note_id}").json()["cards"]
    assert [c["id"] for c in after] == [cards[0]["id"]]

    assert alice.delete(f"/note-types/{type_id}").status_code == 409
    patched = alice.patch(f"/notes/{note_id}", json={"fields": {"Word": "chien"}})
    assert patched.status_code == 200
    assert patched.json()["fields"] == {"Word": "chien", "Meaning": ""}


def test_a_basic_card_has_no_template_or_masks(alice: TestClient) -> None:
    deck_id = _deck(alice)
    card = alice.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "basic", "fields": {"front": "q", "back": "a"}},
    ).json()[0]
    detail = alice.get(f"/cards/{card['id']}").json()
    assert detail["template"] is None and detail["occlusion"] is None
    assert detail["note_type_id"] is None


def test_custom_notes_cannot_use_someone_elses_type(alice: TestClient, app: FastAPI) -> None:
    type_id = alice.post("/note-types", json=VOCAB).json()["id"]
    bob = _member(app, "bob@example.com")
    deck_id = _deck(bob)
    resp = bob.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "custom", "note_type_id": type_id, "fields": {"Word": "x"}},
    )
    assert resp.status_code == 404


def test_occlusion_notes_make_a_card_per_mask(alice: TestClient) -> None:
    deck_id, image_id = _deck(alice), _image(alice)
    resp = alice.post(
        f"/decks/{deck_id}/notes",
        json={
            "note_type": "image_occlusion",
            "fields": {"header": "Heart"},
            "occlusion": {"image_id": image_id, "masks": MASKS},
        },
    )
    assert resp.status_code == 201
    cards = resp.json()
    assert [c["ordinal"] for c in cards] == [1, 2]

    detail = alice.get(f"/cards/{cards[0]['id']}").json()
    assert detail["note_type"] == "image_occlusion" and detail["template"] is None
    assert detail["occlusion"] == {"image_id": image_id, "masks": MASKS}

    moved = [{**MASKS[1], "left": 0.0}, {"id": 3, "left": 0.3, "top": 0.3, "width": 0.1,
                                          "height": 0.1}]
    patched = alice.patch(
        f"/notes/{detail['note_id']}", json={"occlusion": {"image_id": image_id, "masks": moved}}
    )
    assert patched.status_code == 200
    after = {c["ordinal"]: c["id"] for c in patched.json()["cards"]}
    assert set(after) == {2, 3} and after[2] == cards[1]["id"]
    assert patched.json()["occlusion"]["masks"] == moved


@pytest.mark.parametrize(
    "occlusion",
    [
        {"image_id": "IMAGE", "masks": []},
        {"image_id": "IMAGE", "masks": [{**MASKS[0], "left": 1.5}]},
        {"image_id": "IMAGE", "masks": [{**MASKS[0], "width": 0}]},
        {"image_id": "IMAGE", "masks": [{**MASKS[0], "id": 0}]},
        {"image_id": "IMAGE", "masks": [MASKS[0]] * (MAX_MASKS + 1)},
        {"image_id": "IMAGE", "masks": [{**MASKS[0], "left": 0.9, "width": 0.5}]},
    ],
)
def test_invalid_masks_are_refused(alice: TestClient, occlusion: dict[str, object]) -> None:
    deck_id, image_id = _deck(alice), _image(alice)
    resp = alice.post(
        f"/decks/{deck_id}/notes",
        json={
            "note_type": "image_occlusion",
            "fields": {},
            "occlusion": {**occlusion, "image_id": image_id},
        },
    )
    assert resp.status_code == 422


def test_an_occlusion_image_must_be_the_callers(alice: TestClient, app: FastAPI) -> None:
    image_id = _image(alice)
    bob = _member(app, "bob@example.com")
    resp = bob.post(
        f"/decks/{_deck(bob)}/notes",
        json={
            "note_type": "image_occlusion",
            "fields": {},
            "occlusion": {"image_id": image_id, "masks": MASKS},
        },
    )
    assert resp.status_code == 404


def test_occlusion_cards_are_not_graded_by_ai(alice: TestClient) -> None:
    deck_id, image_id = _deck(alice), _image(alice)
    card = alice.post(
        f"/decks/{deck_id}/notes",
        json={
            "note_type": "image_occlusion",
            "fields": {},
            "occlusion": {"image_id": image_id, "masks": MASKS[:1]},
        },
    ).json()[0]
    resp = alice.post(f"/cards/{card['id']}/evaluate", json={"answer": "aorta"})
    assert resp.status_code == 422
    assert "cannot be graded" in resp.json()["detail"]


def test_export_and_deletion_include_note_types(alice: TestClient) -> None:
    type_id = alice.post("/note-types", json=VOCAB).json()["id"]
    deck_id, image_id = _deck(alice), _image(alice)
    alice.post(
        f"/decks/{deck_id}/notes",
        json={"note_type": "custom", "note_type_id": type_id, "fields": {"Word": "chat"}},
    )
    alice.post(
        f"/decks/{deck_id}/notes",
        json={
            "note_type": "image_occlusion",
            "fields": {},
            "occlusion": {"image_id": image_id, "masks": MASKS},
        },
    )
    export = alice.get("/account/export").json()
    assert [t["id"] for t in export["note_types"]] == [type_id]
    by_type = {n["note_type"]: n for n in export["notes"]}
    assert by_type["custom"]["note_type_id"] == type_id
    assert by_type["image_occlusion"]["occlusion"] == {"image_id": image_id, "masks": MASKS}
    assert alice.delete("/account").status_code == 204


def _share(client: TestClient, deck_id: str) -> str:
    token: str = client.post(f"/decks/{deck_id}/shares", json={"allow_clone": True}).json()[
        "token"
    ]
    return token


def _rich_deck(client: TestClient) -> tuple[str, str]:
    type_id = client.post("/note-types", json=VOCAB).json()["id"]
    deck_id, image_id = _deck(client), _image(client)
    for word in ("chat", "chien"):
        client.post(
            f"/decks/{deck_id}/notes",
            json={
                "note_type": "custom",
                "note_type_id": type_id,
                "fields": {"Word": word, "Meaning": "x"},
            },
        )
    for _ in range(2):  # two notes on one image: the image is copied once
        client.post(
            f"/decks/{deck_id}/notes",
            json={
                "note_type": "image_occlusion",
                "fields": {},
                "occlusion": {"image_id": image_id, "masks": MASKS},
            },
        )
    return deck_id, type_id


def test_a_clone_copies_note_types_and_images(alice: TestClient, app: FastAPI) -> None:
    deck_id, type_id = _rich_deck(alice)
    bob = _member(app, "bob@example.com")
    cloned = bob.post(f"/shared/{_share(alice, deck_id)}/clone")
    assert cloned.status_code == 201

    types = bob.get("/note-types").json()
    assert len(types) == 1 and types[0]["id"] != type_id
    assert types[0]["templates"] == alice.get(f"/note-types/{type_id}").json()["templates"]
    export = bob.get("/account/export").json()
    assert len(export["media"]) == 1
    bob_image = export["media"][0]["id"]
    assert bob.get(f"/media/{bob_image}").status_code == 200
    notes = export["notes"]
    assert {n["note_type_id"] for n in notes if n["note_type"] == "custom"} == {types[0]["id"]}
    assert {n["occlusion"]["image_id"] for n in notes if n["occlusion"]} == {bob_image}
    assert len(export["cards"]) == 8  # 2 custom notes x 2 + 2 occlusion notes x 2


def test_a_clone_copies_the_figures_cards_show(alice: TestClient, app: FastAPI) -> None:
    deck_id, image_id = _deck(alice), _image(alice)
    bob = _member(app, "bob@example.com")
    bobs_own = _image(bob)
    fronts = [
        f"![Figure](media:{image_id})\n\nWhich line is dashed?",
        f"![Figure](media:{image_id}) again, ![x](media:{bobs_own}) ![y](media:no-such-id)",
    ]
    for front in fronts:
        alice.post(
            f"/decks/{deck_id}/notes",
            json={"note_type": "basic", "fields": {"front": front, "back": "b"}},
        )
    assert bob.post(f"/shared/{_share(alice, deck_id)}/clone").status_code == 201

    export = bob.get("/account/export").json()
    copies = [m["id"] for m in export["media"] if m["id"] != bobs_own]
    assert len(copies) == 1  # one copy for both cards; media alice does not own is not copied
    assert {n["fields"]["front"] for n in export["notes"]} == {
        f"![Figure](media:{copies[0]})\n\nWhich line is dashed?",
        f"![Figure](media:{copies[0]}) again, ![x](media:{bobs_own}) ![y](media:no-such-id)",
    }
    assert bob.get(f"/media/{copies[0]}").status_code == 200


def test_a_clone_is_bounded_by_image_bytes(
    alice: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    deck_id, _type_id = _rich_deck(alice)
    monkeypatch.setattr(sharing_service, "MAX_CLONE_MEDIA_BYTES", 10)
    bob = _member(app, "bob@example.com")
    assert bob.post(f"/shared/{_share(alice, deck_id)}/clone").status_code == 413
    assert bob.get("/note-types").json() == []


def test_a_clone_respects_the_note_type_limit(
    alice: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    deck_id, _type_id = _rich_deck(alice)
    monkeypatch.setattr(sharing_service, "MAX_NOTE_TYPES_PER_OWNER", 1)
    bob = _member(app, "bob@example.com")
    bob.post("/note-types", json=VOCAB)
    resp = bob.post(f"/shared/{_share(alice, deck_id)}/clone")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "The note type limit has been reached."


def test_copied_images_count_against_the_clone_allowance(
    alice: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    big = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 240  # about 60 KB
    image_id = alice.post("/media", files={"file": ("x.png", big, "image/png")}).json()["id"]
    deck_id = _deck(alice)
    alice.post(
        f"/decks/{deck_id}/notes",
        json={
            "note_type": "image_occlusion",
            "fields": {},
            "occlusion": {"image_id": image_id, "masks": MASKS[:1]},
        },
    )
    # One unit per byte: 60 KB of image is more than the 50,000-card daily allowance.
    monkeypatch.setattr(sharing_service, "CLONE_UNIT_BYTES", 1)
    bob = _member(app, "bob@example.com")
    assert bob.post(f"/shared/{_share(alice, deck_id)}/clone").status_code == 429


def test_a_name_only_type_edit_leaves_cards_alone(alice: TestClient) -> None:
    type_id = alice.post("/note-types", json=VOCAB).json()["id"]
    cards = alice.post(
        f"/decks/{_deck(alice)}/notes",
        json={"note_type": "custom", "note_type_id": type_id, "fields": {"Word": "chat"}},
    ).json()
    assert alice.patch(f"/note-types/{type_id}", json={"name": "Words"}).status_code == 200
    detail = alice.get(f"/cards/{cards[0]['id']}").json()
    assert detail["fields"] == {"Word": "chat", "Meaning": ""}
    renamed = alice.patch(f"/note-types/{type_id}", json={"renamed": {"Word": "Meaning"}})
    assert renamed.status_code == 422
