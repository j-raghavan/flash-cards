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
FIELD_SEPARATOR = "\x1f"
# Newer packages carry collection.anki21; older ones collection.anki2. Prefer the newer.
_COLLECTION_NAMES = ("collection.anki21", "collection.anki2")


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
            return archive.read(candidate)
    raise InvalidPackage("no collection database in package")


def _parse_collection(collection: bytes) -> AnkiPackage:
    with tempfile.NamedTemporaryFile(suffix=".anki2", delete=False) as tmp:
        tmp.write(collection)
        path = tmp.name
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
        "SELECT id, guid, mid, tags, flds FROM notes"
    ):
        guid_text = str(guid)
        guid_by_nid[int(nid)] = guid_text
        notes.append(
            AnkiNote(
                guid=guid_text,
                note_type_id=int(mid),
                fields=tuple(str(flds).split(FIELD_SEPARATOR)),
                tags=tuple(tag for tag in str(tags).split() if tag),
            )
        )

    cards: list[AnkiCard] = []
    for nid, did, ordinal in connection.execute("SELECT nid, did, ord FROM cards"):
        card_guid = guid_by_nid.get(int(nid))
        if card_guid is None:
            continue  # a card with no matching note; skip it
        cards.append(AnkiCard(note_guid=card_guid, deck_id=int(did), ordinal=int(ordinal)))

    return AnkiPackage(
        note_types=note_types, decks=decks, notes=tuple(notes), cards=tuple(cards)
    )


def _parse_models(models_json: object) -> dict[int, AnkiNoteType]:
    raw = _load_json_object(models_json, "models")
    result: dict[int, AnkiNoteType] = {}
    for key, model in raw.items():
        if not isinstance(model, dict):
            raise InvalidPackage("malformed note type")
        fields = tuple(str(field.get("name", "")) for field in model.get("flds", []))
        result[int(key)] = AnkiNoteType(
            id=int(key),
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
        result[int(key)] = AnkiDeck(id=int(key), name=str(deck.get("name", "")))
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
