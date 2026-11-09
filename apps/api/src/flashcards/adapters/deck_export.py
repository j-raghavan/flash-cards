# SPDX-License-Identifier: AGPL-3.0-only
"""Writing a gathered deck tree as .apkg, CSV or JSON (F-IMP-4, ADR 0013).

The .apkg is a zip holding an Anki 2.1 legacy collection (`collection.anki2`,
schema 11) and an empty media manifest, the format Anki and genanki exchange.
Our decks become nested Anki decks, Basic and Cloze notes use two bundled note
types, and each card's schedule and review history are written as SM-2 columns
and revlog rows (domain/anki_schedule.py). CSV carries one row per note and
neutralises spreadsheet formulas; JSON carries everything, schedules included.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime

from flashcards.domain.anki import DECK_SEPARATOR
from flashcards.domain.anki_schedule import sm2_from_schedule
from flashcards.domain.deck_export import DeckExport, ExportedCard, ExportedNote, ExportFormat
from flashcards.domain.notes import NoteType
from flashcards.domain.scheduling import CardSchedule

BASIC_MODEL_ID = 1_700_000_000_001
CLOZE_MODEL_ID = 1_700_000_000_002
DEFAULT_DECK_ID = 1
_SCHEMA_VERSION = 11
_HTML_TAG = re.compile(r"<[^>]*>")
# A cell starting with one of these is a formula to Excel, LibreOffice and Sheets.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
CSV_COLUMNS = ("deck", "note_type", "front", "back", "text", "tags")

_SCHEMA = """
CREATE TABLE col (
    id integer primary key, crt integer not null, mod integer not null,
    scm integer not null, ver integer not null, dty integer not null,
    usn integer not null, ls integer not null, conf text not null,
    models text not null, decks text not null, dconf text not null, tags text not null
);
CREATE TABLE notes (
    id integer primary key, guid text not null, mid integer not null,
    mod integer not null, usn integer not null, tags text not null,
    flds text not null, sfld integer not null, csum integer not null,
    flags integer not null, data text not null
);
CREATE TABLE cards (
    id integer primary key, nid integer not null, did integer not null,
    ord integer not null, mod integer not null, usn integer not null,
    type integer not null, queue integer not null, due integer not null,
    ivl integer not null, factor integer not null, reps integer not null,
    lapses integer not null, left integer not null, odue integer not null,
    odid integer not null, flags integer not null, data text not null
);
CREATE TABLE revlog (
    id integer primary key, cid integer not null, usn integer not null,
    ease integer not null, ivl integer not null, lastIvl integer not null,
    factor integer not null, time integer not null, type integer not null
);
CREATE TABLE graves (usn integer not null, oid integer not null, type integer not null);
CREATE INDEX ix_notes_usn ON notes (usn);
CREATE INDEX ix_cards_usn ON cards (usn);
CREATE INDEX ix_revlog_usn ON revlog (usn);
CREATE INDEX ix_cards_nid ON cards (nid);
CREATE INDEX ix_cards_sched ON cards (did, queue, due);
CREATE INDEX ix_revlog_cid ON revlog (cid);
CREATE INDEX ix_notes_csum ON notes (csum);
"""

_CSS = ".card { font-family: arial; font-size: 20px; text-align: center; }\n.cloze { color: blue; }"
_LATEX_PRE = (
    "\\documentclass[12pt]{article}\n\\special{papersize=3in,5in}\n"
    "\\usepackage[utf8]{inputenc}\n\\usepackage{amssymb,amsmath}\n\\pagestyle{empty}\n"
    "\\setlength{\\parindent}{0in}\n\\begin{document}\n"
)


class FileDeckExportWriter:
    def write(self, export: DeckExport, export_format: ExportFormat) -> bytes:
        if export_format is ExportFormat.APKG:
            return _apkg(export)
        if export_format is ExportFormat.CSV:
            return _csv(export)
        return _json(export)


# --- CSV ---------------------------------------------------------------------


def _safe_cell(value: str) -> str:
    # OWASP CSV injection guidance: a leading quote makes the cell plain text.
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def _csv(export: DeckExport) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for exported in export.notes:
        note = exported.note
        row = (
            DECK_SEPARATOR.join(_anki_deck_part(part) for part in exported.deck),
            note.note_type.value,
            note.field("front") or "",
            note.field("back") or "",
            note.field("text") or "",
            " ".join(note.tags),
        )
        writer.writerow([_safe_cell(cell) for cell in row])
    return buffer.getvalue().encode("utf-8")


# --- JSON --------------------------------------------------------------------

JSON_FORMAT = "flash-cards-deck"
JSON_VERSION = 1


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).isoformat() if moment is not None else None


def _schedule_json(schedule: CardSchedule | None) -> dict[str, object] | None:
    if schedule is None:
        return None
    return {
        "state": schedule.state.value,
        "step": schedule.step,
        "stability": schedule.stability,
        "difficulty": schedule.difficulty,
        "due_at": _iso(schedule.due_at),
        "last_reviewed_at": _iso(schedule.last_reviewed_at),
    }


def _card_json(exported: ExportedCard) -> dict[str, object]:
    card = exported.card
    return {
        "ordinal": card.ordinal,
        "suspended": card.is_suspended,
        "flag": card.flag,
        "schedule": _schedule_json(exported.schedule),
        "reviews": [
            {"rating": int(log.rating), "reviewed_at": _iso(log.reviewed_at)}
            for log in exported.reviews
        ],
    }


def _note_json(exported: ExportedNote) -> dict[str, object]:
    note = exported.note
    return {
        "deck": list(exported.deck),
        "note_type": note.note_type.value,
        "fields": dict(note.fields),
        "tags": list(note.tags),
        "cards": [_card_json(card) for card in exported.cards],
    }


def _json(export: DeckExport) -> bytes:
    document = {
        "format": JSON_FORMAT,
        "version": JSON_VERSION,
        "exported_at": _iso(export.exported_at),
        "decks": [list(path) for path in export.decks],
        "notes": [_note_json(note) for note in export.notes],
    }
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


# --- .apkg -------------------------------------------------------------------


def _anki_deck_part(name: str) -> str:
    # "::" nests decks in Anki, so it cannot appear inside one level's name.
    return name.replace(DECK_SEPARATOR, ":")


def _strip_html(text: str) -> str:
    return _HTML_TAG.sub("", text).strip()


def _checksum(first_field: str) -> int:
    # Anki's duplicate check: the first 8 hex digits of the SHA-1 of the stripped field.
    digest = hashlib.sha1(_strip_html(first_field).encode("utf-8"), usedforsecurity=False)
    return int(digest.hexdigest()[:8], 16)


def _anki_fields(exported: ExportedNote) -> list[str]:
    note = exported.note
    if note.note_type is NoteType.CLOZE:
        return [note.field("text") or "", note.field("extra") or ""]
    return [note.field("front") or "", note.field("back") or ""]


def _anki_ordinal(note_type: NoteType, ordinal: int) -> int:
    # Our cloze ordinal is the deletion number; Anki's card ord is that number minus one.
    return ordinal - 1 if note_type is NoteType.CLOZE else ordinal


def _field_json(name: str, ordinal: int) -> dict[str, object]:
    return {
        "name": name,
        "ord": ordinal,
        "sticky": False,
        "rtl": False,
        "font": "Arial",
        "size": 20,
        "media": [],
    }


def _models(mod_s: int, deck_id: int) -> dict[str, object]:
    common: dict[str, object] = {
        "mod": mod_s,
        "usn": -1,
        "sortf": 0,
        "did": deck_id,
        "css": _CSS,
        "latexPre": _LATEX_PRE,
        "latexPost": "\\end{document}",
        "latexsvg": False,
        "tags": [],
        "vers": [],
    }
    basic = {
        **common,
        "id": BASIC_MODEL_ID,
        "name": "Basic (Flash-Cards)",
        "type": 0,
        "flds": [_field_json("Front", 0), _field_json("Back", 1)],
        "tmpls": [
            {
                "name": "Card 1",
                "ord": 0,
                "qfmt": "{{Front}}",
                "afmt": "{{FrontSide}}\n\n<hr id=answer>\n\n{{Back}}",
                "did": None,
                "bqfmt": "",
                "bafmt": "",
            }
        ],
        "req": [[0, "any", [0]]],
    }
    cloze = {
        **common,
        "id": CLOZE_MODEL_ID,
        "name": "Cloze (Flash-Cards)",
        "type": 1,
        "flds": [_field_json("Text", 0), _field_json("Back Extra", 1)],
        "tmpls": [
            {
                "name": "Cloze",
                "ord": 0,
                "qfmt": "{{cloze:Text}}",
                "afmt": "{{cloze:Text}}<br>\n{{Back Extra}}",
                "did": None,
                "bqfmt": "",
                "bafmt": "",
            }
        ],
    }
    return {str(BASIC_MODEL_ID): basic, str(CLOZE_MODEL_ID): cloze}


def _deck_json(deck_id: int, name: str, mod_s: int) -> dict[str, object]:
    return {
        "id": deck_id,
        "name": name,
        "mod": mod_s,
        "usn": -1,
        "desc": "",
        "dyn": 0,
        "conf": 1,
        "collapsed": False,
        "browserCollapsed": False,
        "newToday": [0, 0],
        "revToday": [0, 0],
        "lrnToday": [0, 0],
        "timeToday": [0, 0],
        "extendNew": 0,
        "extendRev": 0,
    }


def _deck_config(mod_s: int) -> dict[str, object]:
    return {
        "1": {
            "id": 1,
            "name": "Default",
            "mod": mod_s,
            "usn": 0,
            "maxTaken": 60,
            "autoplay": True,
            "timer": 0,
            "replayq": True,
            "dyn": False,
            "new": {
                "delays": [1.0, 10.0],
                "ints": [1, 4, 0],
                "initialFactor": 2500,
                "order": 1,
                "perDay": 20,
                "bury": False,
            },
            "lapse": {
                "delays": [10.0],
                "mult": 0.0,
                "minInt": 1,
                "leechFails": 8,
                "leechAction": 1,
            },
            "rev": {
                "perDay": 200,
                "ease4": 1.3,
                "ivlFct": 1.0,
                "maxIvl": 36500,
                "bury": False,
                "hardFactor": 1.2,
            },
        }
    }


def _collection_conf(next_position: int) -> dict[str, object]:
    return {
        "activeDecks": [DEFAULT_DECK_ID],
        "curDeck": DEFAULT_DECK_ID,
        "newSpread": 0,
        "collapseTime": 1200,
        "timeLim": 0,
        "estTimes": True,
        "dueCounts": True,
        "curModel": str(BASIC_MODEL_ID),
        "nextPos": next_position,
        "sortType": "noteFld",
        "sortBackwards": False,
        "addToCur": True,
    }


def _origin(export: DeckExport) -> datetime:
    """Day zero for review dues: the earliest of now and every due date, at midnight UTC.

    Starting no later than any due date keeps every review card's due day number
    non-negative.
    """
    moments = [export.exported_at] + [
        card.schedule.due_at
        for note in export.notes
        for card in note.cards
        if card.schedule is not None
    ]
    earliest = min(moment.astimezone(UTC) for moment in moments)
    return earliest.replace(hour=0, minute=0, second=0, microsecond=0)


def _unique_ids(start: int) -> Iterator[int]:
    current = start
    while True:
        yield current
        current += 1


def _apkg(export: DeckExport) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    try:
        connection = sqlite3.connect(path)
        try:
            _fill_collection(connection, export)
            connection.commit()
        finally:
            connection.close()
        with open(path, "rb") as handle:
            collection = handle.read()
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("collection.anki2", collection)
        archive.writestr("media", "{}")
    return buffer.getvalue()


def _fill_collection(connection: sqlite3.Connection, export: DeckExport) -> None:
    connection.executescript(_SCHEMA)
    now = export.exported_at.astimezone(UTC)
    mod_s = int(now.timestamp())
    mod_ms = int(now.timestamp() * 1000)
    origin = _origin(export)
    ids = _unique_ids(mod_ms)

    deck_ids = {path: next(ids) for path in export.decks}
    decks: dict[str, object] = {
        str(DEFAULT_DECK_ID): _deck_json(DEFAULT_DECK_ID, "Default", mod_s)
    }
    for path, deck_id in deck_ids.items():
        name = DECK_SEPARATOR.join(_anki_deck_part(part) for part in path)
        decks[str(deck_id)] = _deck_json(deck_id, name, mod_s)
    first_deck = next(iter(deck_ids.values()), DEFAULT_DECK_ID)

    new_position = 0
    used_revlog_ids: set[int] = set()
    for exported in export.notes:
        note = exported.note
        note_id = next(ids)
        fields = _anki_fields(exported)
        is_cloze = note.note_type is NoteType.CLOZE
        connection.execute(
            "INSERT INTO notes VALUES (?, ?, ?, ?, -1, ?, ?, ?, ?, 0, '')",
            (
                note_id,
                note.id,  # a stable guid: re-importing into Anki updates, not duplicates
                CLOZE_MODEL_ID if is_cloze else BASIC_MODEL_ID,
                mod_s,
                f" {' '.join(note.tags)} " if note.tags else "",
                "\x1f".join(fields),
                _strip_html(fields[0]),
                _checksum(fields[0]),
            ),
        )
        for card in exported.cards:
            card_id = next(ids)
            ratings = [log.rating for log in card.reviews]
            sm2 = sm2_from_schedule(
                card.schedule,
                suspended=card.card.is_suspended,
                new_position=new_position,
                origin=origin,
                ratings=ratings,
            )
            new_position += card.schedule is None
            connection.execute(
                "INSERT INTO cards VALUES (?, ?, ?, ?, ?, -1, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, '')",
                (
                    card_id,
                    note_id,
                    deck_ids[exported.deck],
                    _anki_ordinal(note.note_type, card.card.ordinal),
                    mod_s,
                    sm2.card_type,
                    sm2.queue,
                    sm2.due,
                    sm2.interval,
                    sm2.factor,
                    sm2.reps,
                    sm2.lapses,
                    card.card.flag,
                ),
            )
            for index, log in enumerate(card.reviews):
                revlog_id = int(log.reviewed_at.timestamp() * 1000)
                while revlog_id in used_revlog_ids:  # the revlog id is its primary key
                    revlog_id += 1
                used_revlog_ids.add(revlog_id)
                connection.execute(
                    "INSERT INTO revlog VALUES (?, ?, -1, ?, 0, 0, 0, 0, ?)",
                    (revlog_id, card_id, int(log.rating), 0 if index == 0 else 1),
                )

    connection.execute(
        "INSERT INTO col VALUES (1, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?, '{}')",
        (
            int(origin.timestamp()),
            mod_ms,
            mod_ms,
            _SCHEMA_VERSION,
            json.dumps(_collection_conf(new_position)),
            json.dumps(_models(mod_s, first_deck)),
            json.dumps(decks),
            json.dumps(_deck_config(mod_s)),
        ),
    )

