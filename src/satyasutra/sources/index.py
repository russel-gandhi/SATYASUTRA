"""SQLite FTS5 index of official passages: what Tier 1 publishers have said, with where and when we fetched it.

Only plain text goes in. A search query is reduced to alphanumeric words before it reaches FTS5, so claim text can never act as
FTS5 syntax. Results are ranked by bm25 and each keeps its source, link, publication time and fetch time.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

WORD = re.compile(r"\w+", re.UNICODE)
META_REFRESHED = "refreshed_at"


@dataclass
class Passage:
    source_id: str
    publisher: str
    tier: int
    url: str
    title: str
    text: str                       # the exact passage cited as evidence
    sha256: str
    published_at: datetime | None
    fetched_at: datetime


class OfficialIndex:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(title, text, sha UNINDEXED, source_id UNINDEXED, publisher UNINDEXED, "
                       "tier UNINDEXED, url UNINDEXED, published_at UNINDEXED, fetched_at UNINDEXED, tokenize='unicode61')")
            db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            db.commit()

    def add(self, p: Passage) -> bool:
        """Insert a passage unless the same text is already indexed. True when it was new."""
        with closing(sqlite3.connect(self.path)) as db:
            if db.execute("SELECT 1 FROM passages WHERE sha = ?", (p.sha256,)).fetchone():
                return False
            db.execute("INSERT INTO passages (title, text, sha, source_id, publisher, tier, url, published_at, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (p.title, p.text, p.sha256, p.source_id, p.publisher, p.tier, p.url,
                        p.published_at.isoformat() if p.published_at else "", p.fetched_at.isoformat()))
            db.commit()
            return True

    def search(self, query: str, limit: int, min_chars: int) -> list[Passage]:
        words = [w for w in WORD.findall(query.casefold()) if len(w) >= min_chars]
        if not words:
            return []
        match = " OR ".join(f'"{w}"' for w in dict.fromkeys(words))
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT title, text, sha, source_id, publisher, tier, url, published_at, fetched_at FROM passages "
                              "WHERE passages MATCH ? ORDER BY bm25(passages) LIMIT ?", (match, limit)).fetchall()
        return [Passage(r[3], r[4], int(r[5]), r[6], r[0], r[1], r[2], datetime.fromisoformat(r[7]) if r[7] else None, datetime.fromisoformat(r[8])) for r in rows]

    def count(self) -> int:
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT count(*) FROM passages").fetchone()[0]

    def recent(self, limit: int) -> list[Passage]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT title, text, sha, source_id, publisher, tier, url, published_at, fetched_at FROM passages "
                              "ORDER BY NULLIF(published_at, '') DESC LIMIT ?", (limit,)).fetchall()
        return [Passage(r[3], r[4], int(r[5]), r[6], r[0], r[1], r[2], datetime.fromisoformat(r[7]) if r[7] else None, datetime.fromisoformat(r[8])) for r in rows]

    def publishers(self) -> dict[str, int]:
        with closing(sqlite3.connect(self.path)) as db:
            return dict(db.execute("SELECT publisher, count(*) FROM passages GROUP BY publisher").fetchall())

    def texts(self) -> list[str]:
        with closing(sqlite3.connect(self.path)) as db:
            return [r[0] for r in db.execute("SELECT text FROM passages")]

    def span(self) -> tuple[int, str, str]:
        """How many passages are indexed and the oldest and newest publication times (ISO, UTC), for saying how far back a search looked."""
        with closing(sqlite3.connect(self.path)) as db:
            n, lo, hi = db.execute("SELECT count(*), min(NULLIF(published_at, '')), max(NULLIF(published_at, '')) FROM passages").fetchone()
        return n, lo or "", hi or ""

    def refreshed_at(self) -> datetime | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT value FROM meta WHERE key = ?", (META_REFRESHED,)).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def mark_refreshed(self, at: datetime) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (META_REFRESHED, at.isoformat()))
            db.commit()
