# SPDX-License-Identifier: AGPL-3.0-only
"""The defensive .apkg reader, against minimal packages built in-process."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import zipfile
from collections.abc import Sequence

import pytest

from flashcards.adapters import apkg
from flashcards.adapters.apkg import ApkgReader
from flashcards.domain.anki import InvalidPackage, UnsupportedPackage

SEP = "\x1f"
MODELS = {
    "1": {"name": "Basic", "type": 0, "flds": [{"name": "Front"}, {"name": "Back"}]},
    "2": {"name": "Cloze", "type": 1, "flds": [{"name": "Text"}]},
}
DECKS = {"1": {"name": "Default"}, "100": {"name": "Biology"}}
NOTES = [
    (10, "g1", 1, " tag1 tag2 ", f"Capital of France?{SEP}Paris"),
    (11, "g2", 2, "", "The {{c1::cell}} is the unit of life"),
]
CARDS = [(10, 100, 0), (11, 100, 0), (99, 100, 0)]  # the nid=99 card has no note


def _as_text(value: object) -> str | None:
    # None becomes a real SQL NULL; a str passes through; anything else is JSON-encoded.
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value)


def _collection(
    *,
    models: object = MODELS,
    decks: object = DECKS,
    with_row: bool = True,
    note_rows: Sequence[tuple[object, ...]] | None = None,
    card_rows: Sequence[tuple[object, ...]] | None = None,
) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE col (models TEXT, decks TEXT)")
        if with_row:
            conn.execute(
                "INSERT INTO col (models, decks) VALUES (?, ?)",
                (_as_text(models), _as_text(decks)),
            )
        conn.execute(
            "CREATE TABLE notes (id INTEGER, guid TEXT, mid INTEGER, tags TEXT, flds TEXT)"
        )
        conn.executemany(
            "INSERT INTO notes VALUES (?, ?, ?, ?, ?)",
            NOTES if note_rows is None else note_rows,
        )
        conn.execute("CREATE TABLE cards (nid INTEGER, did INTEGER, ord INTEGER)")
        conn.executemany(
            "INSERT INTO cards VALUES (?, ?, ?)", CARDS if card_rows is None else card_rows
        )
        conn.commit()
        conn.close()
        with open(path, "rb") as handle:
            return handle.read()
    finally:
        os.unlink(path)


def _apkg(
    collection: bytes | None = None,
    *,
    name: str = "collection.anki21",
    extra: dict[str, bytes] | None = None,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if collection is not None:
            archive.writestr(name, collection)
        archive.writestr("media", "{}")
        for entry_name, payload in (extra or {}).items():
            archive.writestr(entry_name, payload)
    return buffer.getvalue()


def test_reads_note_types_decks_notes_and_cards() -> None:
    package = ApkgReader().read(_apkg(_collection()))

    assert package.note_types[1].name == "Basic" and package.note_types[1].is_cloze is False
    assert package.note_types[2].is_cloze is True
    assert package.decks[100].name == "Biology"

    by_guid = {note.guid: note for note in package.notes}
    assert by_guid["g1"].fields == ("Capital of France?", "Paris")
    assert by_guid["g1"].tags == ("tag1", "tag2")
    assert by_guid["g2"].note_type_id == 2
    # The orphan card (nid=99) is dropped; the two real ones remain.
    assert len(package.cards) == 2
    assert {card.note_guid for card in package.cards} == {"g1", "g2"}


def test_falls_back_to_the_older_collection_name() -> None:
    package = ApkgReader().read(_apkg(_collection(), name="collection.anki2"))
    assert len(package.notes) == 2


def test_not_a_zip_is_invalid() -> None:
    with pytest.raises(InvalidPackage):
        ApkgReader().read(b"this is not a zip")


def test_missing_collection_is_invalid() -> None:
    with pytest.raises(InvalidPackage):
        ApkgReader().read(_apkg(collection=None))


def test_empty_collection_is_invalid() -> None:
    with pytest.raises(InvalidPackage):
        ApkgReader().read(_apkg(_collection(with_row=False)))


def test_path_traversal_entry_is_rejected() -> None:
    with pytest.raises(UnsupportedPackage):
        ApkgReader().read(_apkg(_collection(), extra={"../escape.txt": b"x"}))


def test_absolute_path_entry_is_rejected() -> None:
    with pytest.raises(UnsupportedPackage):
        ApkgReader().read(_apkg(_collection(), extra={"/etc/evil": b"x"}))


def test_zip_bomb_ratio_is_rejected() -> None:
    # A very compressible payload trips the compression-ratio guard.
    with pytest.raises(UnsupportedPackage):
        ApkgReader().read(_apkg(_collection(), extra={"bomb": b"\x00" * 200_000}))


def test_too_many_entries_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apkg, "MAX_ENTRIES", 1)
    with pytest.raises(UnsupportedPackage):
        ApkgReader().read(_apkg(_collection()))


def test_too_large_uncompressed_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apkg, "MAX_TOTAL_UNCOMPRESSED", 10)
    with pytest.raises(UnsupportedPackage):
        ApkgReader().read(_apkg(_collection()))


def test_bad_models_json_is_invalid() -> None:
    with pytest.raises(InvalidPackage):
        ApkgReader().read(_apkg(_collection(models="not json")))


def test_models_not_an_object_is_invalid() -> None:
    with pytest.raises(InvalidPackage):
        ApkgReader().read(_apkg(_collection(models="[1, 2, 3]")))


def test_null_decks_is_invalid() -> None:
    with pytest.raises(InvalidPackage):
        ApkgReader().read(_apkg(_collection(decks=None)))


def test_malformed_model_entry_is_invalid() -> None:
    with pytest.raises(InvalidPackage):
        ApkgReader().read(_apkg(_collection(models={"1": "not-a-dict"})))


def test_malformed_deck_entry_is_invalid() -> None:
    with pytest.raises(InvalidPackage):
        ApkgReader().read(_apkg(_collection(decks={"1": "not-a-dict"})))


def test_corrupt_database_is_invalid() -> None:
    # A file named like a collection but not a valid SQLite database.
    with pytest.raises(InvalidPackage):
        ApkgReader().read(_apkg(b"SQLite format 3\x00 but corrupt"))


def test_non_numeric_note_type_key_is_invalid() -> None:
    bad_models = {"abc": {"name": "X", "type": 0, "flds": [{"name": "F"}]}}
    with pytest.raises(InvalidPackage):
        ApkgReader().read(_apkg(_collection(models=bad_models)))


def test_models_flds_not_a_list_is_invalid() -> None:
    bad_models = {"1": {"name": "X", "type": 0, "flds": "not-a-list"}}
    with pytest.raises(InvalidPackage):
        ApkgReader().read(_apkg(_collection(models=bad_models)))


def test_non_integer_note_mid_is_invalid() -> None:
    rows = [(10, "g1", "not-an-int", "", f"Q{SEP}A")]  # mid is text in an int column
    with pytest.raises(InvalidPackage):
        ApkgReader().read(_apkg(_collection(note_rows=rows)))


def test_null_card_nid_is_invalid() -> None:
    rows = [(None, 100, 0)]  # nid is NULL -> not int/str
    with pytest.raises(InvalidPackage):
        ApkgReader().read(_apkg(_collection(card_rows=rows)))


def test_too_many_notes_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apkg, "MAX_NOTES", 1)
    rows = [(10, "g1", 1, "", f"Q{SEP}A"), (11, "g2", 1, "", f"Q2{SEP}A2")]
    with pytest.raises(UnsupportedPackage):
        ApkgReader().read(_apkg(_collection(note_rows=rows)))


def test_too_many_cards_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apkg, "MAX_CARDS", 1)
    with pytest.raises(UnsupportedPackage):
        ApkgReader().read(_apkg(_collection()))  # the fixture has 3 cards


def test_oversized_collection_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apkg, "MAX_COLLECTION_BYTES", 10)
    with pytest.raises(UnsupportedPackage):
        ApkgReader().read(_apkg(_collection()))  # a real SQLite file exceeds 10 bytes
