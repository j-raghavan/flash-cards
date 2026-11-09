# SPDX-License-Identifier: AGPL-3.0-only
"""Defensive reader for Anki .apkg packages (F-IMP-1, NF-SEC-2).

An .apkg is a zip holding a SQLite collection and numbered media files. The
archive is untrusted, so the reader guards against path traversal and zip bombs,
and opens the collection database read-only and immutable, running only fixed
queries against real tables, with untrusted schema functions off and a cap on
the work any query can do. Nothing from the package is ever executed. Besides
notes and cards it reads each card's SM-2 scheduling columns and the review log
(F-IMP-2), both bounded.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import PurePosixPath

from flashcards.domain.anki import (
    AnkiCard,
    AnkiDeck,
    AnkiNote,
    AnkiNoteType,
    AnkiPackage,
    AnkiReview,
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
# Review history is kept per answer, so it is the largest table; past this many rows
# it is dropped (cards fall back to their SM-2 columns) rather than held in memory.
MAX_REVIEWS = 300_000
# SQLite calls the progress handler every _SQL_STEP_GRANULARITY virtual-machine
# steps; after MAX_SQL_STEPS calls in total, across every query on the collection,
# the running query is interrupted. Reading a collection at the row limits above
# takes a small fraction of this budget.
_SQL_STEP_GRANULARITY = 1_000
MAX_SQL_STEPS = 1_000_000
FIELD_SEPARATOR = "\x1f"
# Newer packages carry collection.anki21; older ones collection.anki2. Prefer the newer.
_COLLECTION_NAMES = ("collection.anki21", "collection.anki2")
# Anki 2.1.50+ writes its own zstd-compressed collection.anki21b by default, next to
# a stub collection.anki2 that only holds a "please update Anki" note. Importing the
# stub would look like success, so such a package is refused with a clear reason.
_NEW_FORMAT_COLLECTION = "collection.anki21b"
_NOT_MEDIA = frozenset({*_COLLECTION_NAMES, _NEW_FORMAT_COLLECTION, "media", "meta"})
# A real collection has these; minimal or very old ones may not, and then every
# card is read as new and no history is kept.
_SCHEDULING_COLUMNS = frozenset(
    {"id", "type", "queue", "due", "ivl", "factor", "reps", "lapses", "odid", "odue", "flags"}
)
_REVLOG_COLUMNS = frozenset({"id", "cid", "ease", "type"})
_FLAG_BITS = 0b111  # the low three bits of cards.flags are the flag colour


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
            media_count = sum(1 for name in archive.namelist() if name not in _NOT_MEDIA)
        package = _parse_collection(collection)
        return replace(package, media_count=media_count)


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
    if _NEW_FORMAT_COLLECTION in names:
        raise UnsupportedPackage(
            "exported in Anki's newer format: export again with "
            '"Support older Anki versions" ticked'
        )
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


def _abort_after(budget: int) -> Callable[[], int]:
    remaining = budget

    def tick() -> int:
        nonlocal remaining
        remaining -= 1
        return 1 if remaining < 0 else 0  # non-zero interrupts the running statement

    return tick


def _guard_schema(connection: sqlite3.Connection) -> None:
    # The queries below name these tables; a hostile file could make one a view
    # over an expensive recursive query, so only real tables are accepted.
    for name, kind in connection.execute(
        "SELECT name, type FROM sqlite_master WHERE name IN ('col', 'notes', 'cards', 'revlog')"
    ):
        if kind != "table":
            raise UnsupportedPackage(f"{name} is not a table")


def _columns(connection: sqlite3.Connection, table: str) -> frozenset[str]:
    return frozenset(
        str(row[0])
        for row in connection.execute("SELECT name FROM pragma_table_info(?)", (table,))
    )


def _read_rows(connection: sqlite3.Connection) -> AnkiPackage:
    # Defence in depth on top of read-only/immutable: no functions from the
    # schema, and a hard cap on the total work the reads can make SQLite do.
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.set_progress_handler(_abort_after(MAX_SQL_STEPS), _SQL_STEP_GRANULARITY)
    _guard_schema(connection)
    created_at_s: int | None = None
    if "crt" in _columns(connection, "col"):
        header = connection.execute("SELECT models, decks, crt FROM col LIMIT 1").fetchone()
        if header is not None:
            created_at_s = _require_int(header[2], "collection creation time")
    else:
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

    cards = _read_cards(connection, guid_by_nid)
    reviews, truncated = _read_reviews(connection, {card.id for card in cards if card.id})
    return AnkiPackage(
        note_types=note_types,
        decks=decks,
        notes=tuple(notes),
        cards=tuple(cards),
        reviews=reviews,
        created_at_s=created_at_s,
        history_truncated=truncated,
    )


def _read_cards(connection: sqlite3.Connection, guid_by_nid: dict[int, str]) -> list[AnkiCard]:
    with_state = _columns(connection, "cards") >= _SCHEDULING_COLUMNS
    query = (
        "SELECT nid, did, ord, id, type, queue, due, ivl, factor, reps, lapses, odid, odue, "
        "flags FROM cards ORDER BY id LIMIT ?"
        if with_state
        else "SELECT nid, did, ord FROM cards LIMIT ?"
    )
    cards: list[AnkiCard] = []
    for row in connection.execute(query, (MAX_CARDS + 1,)):
        card_guid = guid_by_nid.get(_require_int(row[0], "card note id"))
        if card_guid is None:
            continue  # a card with no matching note; skip it
        values = [_require_int(value, "card column") for value in row[1:]]
        if with_state:
            (did, ordinal, card_id, kind, queue, due, ivl, factor, reps, lapses, odid, odue,
             flags) = values
            cards.append(
                AnkiCard(
                    note_guid=card_guid,
                    deck_id=did,
                    ordinal=ordinal,
                    id=card_id,
                    card_type=kind,
                    queue=queue,
                    due=due,
                    interval=ivl,
                    factor=factor,
                    reps=reps,
                    lapses=lapses,
                    original_deck_id=odid,
                    original_due=odue,
                    flag=flags & _FLAG_BITS,
                )
            )
        else:
            cards.append(AnkiCard(note_guid=card_guid, deck_id=values[0], ordinal=values[1]))
    if len(cards) > MAX_CARDS:
        raise UnsupportedPackage("collection has too many cards")
    return cards


def _read_reviews(
    connection: sqlite3.Connection, card_ids: set[int]
) -> tuple[tuple[AnkiReview, ...], bool]:
    """The revlog rows of known cards, oldest first, and whether it was over the limit.

    A history over MAX_REVIEWS is dropped entirely rather than failing the import:
    the cards then take their schedule from their SM-2 columns instead.
    """
    if not _columns(connection, "revlog") >= _REVLOG_COLUMNS:
        return (), False
    rows = connection.execute(
        "SELECT id, cid, ease, type FROM revlog ORDER BY id LIMIT ?", (MAX_REVIEWS + 1,)
    ).fetchall()
    if len(rows) > MAX_REVIEWS:
        return (), True
    reviews = []
    for row in rows:
        reviewed_at_ms, card_id, ease, kind = (_require_int(v, "revlog column") for v in row)
        if card_id in card_ids:
            reviews.append(AnkiReview(card_id, reviewed_at_ms, ease, kind))
    return tuple(reviews), False


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
        templates = model.get("tmpls", [])
        model_id = _require_int(key, "note type id")
        result[model_id] = AnkiNoteType(
            id=model_id,
            name=str(model.get("name", "")),
            is_cloze=model.get("type", 0) == 1,
            field_names=fields,
            template_count=max(1, len(templates)) if isinstance(templates, list) else 1,
        )
    return result


def _parse_decks(decks_json: object) -> dict[int, AnkiDeck]:
    raw = _load_json_object(decks_json, "decks")
    result: dict[int, AnkiDeck] = {}
    for key, deck in raw.items():
        if not isinstance(deck, dict):
            raise InvalidPackage("malformed deck")
        deck_id = _require_int(key, "deck id")
        result[deck_id] = AnkiDeck(
            id=deck_id, name=str(deck.get("name", "")), is_filtered=bool(deck.get("dyn", 0))
        )
    return result


def _load_json_object(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, str | bytes):
        raise InvalidPackage(f"{what} is not text")
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, RecursionError) as exc:  # deeply nested JSON recurses
        raise InvalidPackage(f"bad {what} json") from exc
    if not isinstance(parsed, dict):
        raise InvalidPackage(f"{what} is not an object")
    return parsed
