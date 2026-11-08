# SPDX-License-Identifier: AGPL-3.0-only
"""Defensive reader for Anki .apkg packages (F-IMP-1, NF-SEC-2).

An .apkg is a zip holding a SQLite collection and numbered media files. The
archive is untrusted, so the reader guards against path traversal and zip bombs,
and opens the collection database read-only and immutable, running only fixed
queries. Nothing from the package is ever executed.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from pathlib import PurePosixPath

from flashcards.domain.anki import (
    AnkiCard,
    AnkiDeck,
    AnkiNote,
    AnkiNoteType,
    AnkiPackage,
    InvalidPackage,
    UnsupportedPackage,
)

MAX_ENTRIES = 10_000
MAX_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
# A real bound on the decompressed collection (not trusting the zip's self-reported
# sizes), plus caps on row counts so a valid-but-hostile DB can't amplify into a
# multi-GB object graph.
MAX_COLLECTION_BYTES = 200 * 1024 * 1024
MAX_NOTES = 100_000
MAX_CARDS = 200_000
FIELD_SEPARATOR = "\x1f"
# Newer packages carry collection.anki21; older ones collection.anki2. Prefer the newer.
_COLLECTION_NAMES = ("collection.anki21", "collection.anki2")


def _require_int(value: object, what: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise InvalidPackage(f"invalid integer for {what}") from exc
    raise InvalidPackage(f"invalid integer for {what}")


class ApkgReader:
    def read(self, data: bytes) -> AnkiPackage:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise InvalidPackage("not a zip archive") from exc
        with archive:
            _guard_archive(archive)
            collection = _read_collection(archive)
        return _parse_collection(collection)


def _guard_archive(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise UnsupportedPackage("archive has too many entries")
    total = 0
    for info in infos:
        parts = PurePosixPath(info.filename).parts
        if info.filename.startswith("/") or ".." in parts:
            raise UnsupportedPackage("unsafe path in archive")
        total += info.file_size
        if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise UnsupportedPackage("suspicious compression ratio")
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise UnsupportedPackage("archive is too large")


def _read_collection(archive: zipfile.ZipFile) -> bytes:
    names = set(archive.namelist())
    for candidate in _COLLECTION_NAMES:
        if candidate in names:
            # Bound the ACTUAL decompressed output, rather than trusting the zip's
            # self-reported sizes (which the guard above can only check advisorily).
            with archive.open(candidate) as stream:
                data = stream.read(MAX_COLLECTION_BYTES + 1)
            if len(data) > MAX_COLLECTION_BYTES:
                raise UnsupportedPackage("collection database is too large")
            return data
    raise InvalidPackage("no collection database in package")


def _parse_collection(collection: bytes) -> AnkiPackage:
    with tempfile.NamedTemporaryFile(suffix=".anki2", delete=False) as tmp:
        path = tmp.name  # captured before the write, so the finally always cleans up
        tmp.write(collection)
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            return _read_rows(connection)
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise InvalidPackage("unreadable collection database") from exc
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


def _read_rows(connection: sqlite3.Connection) -> AnkiPackage:
    header = connection.execute("SELECT models, decks FROM col LIMIT 1").fetchone()
    if header is None:
        raise InvalidPackage("empty collection")
    note_types = _parse_models(header[0])
    decks = _parse_decks(header[1])

    notes: list[AnkiNote] = []
    guid_by_nid: dict[int, str] = {}
    for nid, guid, mid, tags, flds in connection.execute(
        "SELECT id, guid, mid, tags, flds FROM notes LIMIT ?", (MAX_NOTES + 1,)
    ):
        guid_text = str(guid)
        guid_by_nid[_require_int(nid, "note id")] = guid_text
        notes.append(
            AnkiNote(
                guid=guid_text,
                note_type_id=_require_int(mid, "note type id"),
                fields=tuple(str(flds).split(FIELD_SEPARATOR)),
                tags=tuple(tag for tag in str(tags).split() if tag),
            )
        )
    if len(notes) > MAX_NOTES:
        raise UnsupportedPackage("collection has too many notes")

    cards: list[AnkiCard] = []
    for nid, did, ordinal in connection.execute(
        "SELECT nid, did, ord FROM cards LIMIT ?", (MAX_CARDS + 1,)
    ):
        card_guid = guid_by_nid.get(_require_int(nid, "card note id"))
        if card_guid is None:
            continue  # a card with no matching note; skip it
        cards.append(
            AnkiCard(
                note_guid=card_guid,
                deck_id=_require_int(did, "deck id"),
                ordinal=_require_int(ordinal, "ordinal"),
            )
        )
    if len(cards) > MAX_CARDS:
        raise UnsupportedPackage("collection has too many cards")

    return AnkiPackage(
        note_types=note_types, decks=decks, notes=tuple(notes), cards=tuple(cards)
    )


def _parse_models(models_json: object) -> dict[int, AnkiNoteType]:
    raw = _load_json_object(models_json, "models")
    result: dict[int, AnkiNoteType] = {}
    for key, model in raw.items():
        if not isinstance(model, dict):
            raise InvalidPackage("malformed note type")
        flds = model.get("flds", [])
        if not isinstance(flds, list):
            raise InvalidPackage("malformed note type fields")
        fields = tuple(
            str(field.get("name", "")) for field in flds if isinstance(field, dict)
        )
        model_id = _require_int(key, "note type id")
        result[model_id] = AnkiNoteType(
            id=model_id,
            name=str(model.get("name", "")),
            is_cloze=model.get("type", 0) == 1,
            field_names=fields,
        )
    return result


def _parse_decks(decks_json: object) -> dict[int, AnkiDeck]:
    raw = _load_json_object(decks_json, "decks")
    result: dict[int, AnkiDeck] = {}
    for key, deck in raw.items():
        if not isinstance(deck, dict):
            raise InvalidPackage("malformed deck")
        deck_id = _require_int(key, "deck id")
        result[deck_id] = AnkiDeck(id=deck_id, name=str(deck.get("name", "")))
    return result


def _load_json_object(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, str | bytes):
        raise InvalidPackage(f"{what} is not text")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise InvalidPackage(f"bad {what} json") from exc
    if not isinstance(parsed, dict):
        raise InvalidPackage(f"{what} is not an object")
    return parsed
