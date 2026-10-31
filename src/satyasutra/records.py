"""Investigation records and built reports, kept in one small SQLite file.

A record is what the server itself produced for one submission (claim verdicts, an image check, a video check, a voice note, a notice, a document, a web page).
A report is built only from records the server holds, never from anything a client sends, so it cannot cite a source the system did not actually retrieve.
Reports are immutable: the id is the hash of the content, so a shared link always shows the same thing.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .schemas.core import sha256_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, body TEXT NOT NULL);
"""


class RecordStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(SCHEMA)

    def add_record(self, kind: str, body: dict, max_bytes: int) -> str:
        raw = json.dumps(body, ensure_ascii=False)
        if len(raw.encode("utf-8")) > max_bytes:
            raise ValueError("record too large")
        rid = uuid.uuid4().hex[:16]
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO records VALUES (?, ?, ?, ?)", (rid, kind, datetime.now(timezone.utc).isoformat(), raw))
        return rid

    def get_record(self, rid: str) -> dict | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT kind, created_at, body FROM records WHERE id = ?", (rid,)).fetchone()
        return {"id": rid, "kind": row[0], "created_at": row[1], "body": json.loads(row[2])} if row else None

    def add_report(self, report: dict) -> str:
        raw = json.dumps(report, ensure_ascii=False, sort_keys=True)
        rid = sha256_text(raw)[:16]
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR IGNORE INTO reports VALUES (?, ?, ?)", (rid, datetime.now(timezone.utc).isoformat(), raw))
        return rid

    def get_report(self, rid: str) -> dict | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT body FROM reports WHERE id = ?", (rid,)).fetchone()
        return json.loads(row[0]) if row else None
