"""SQLite store of image hashes and links. Only hashes, URLs and the fact-checker's own words are kept; never the image.

A hit means "this image appeared in a fact-checker's article". It does not say the claim is false, and it is a provenance
lead, not a verdict (see docs/provenance_index.md).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .fingerprint import PDQ_BITS, PHASH_BITS, Fingerprint, hamming

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY,
    sha256       TEXT NOT NULL,
    phash        TEXT NOT NULL,
    pdq          TEXT NOT NULL,
    pdq_quality  INTEGER NOT NULL,
    image_url    TEXT NOT NULL,
    article_url  TEXT NOT NULL,
    title        TEXT NOT NULL,
    publisher    TEXT NOT NULL,
    lang         TEXT NOT NULL,
    reviewed_at  TEXT,
    labels       TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    UNIQUE (sha256, article_url)
);
CREATE INDEX IF NOT EXISTS items_article ON items (article_url, image_url);
"""


@dataclass(frozen=True)
class Source:
    image_url: str
    article_url: str
    title: str
    publisher: str
    lang: str
    reviewed_at: datetime | None
    labels: str                   # the feed's category tags, joined; NOT a structured rating (feeds carry none)


@dataclass(frozen=True)
class Match:
    distance: int                 # PDQ Hamming distance, 0 to 256, the smaller of original and mirrored
    phash_distance: int
    mirrored: bool
    exact: bool                   # byte-identical file
    source: Source
    retrieved_at: datetime


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hex(value: int, bits: int) -> str:
    return f"{value:0{bits // 4}x}"


class ImageIndex:
    def __init__(self, path: str | Path, clock: Callable[[], datetime] = _utc_now):
        self.path, self._clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def add(self, fp: Fingerprint, source: Source) -> bool:
        """True when a new row was written, False when this image from this article was already indexed."""
        with closing(self._connect()) as con:
            cur = con.execute(
                "INSERT OR IGNORE INTO items (sha256, phash, pdq, pdq_quality, image_url, article_url, title, publisher, lang,"
                " reviewed_at, labels, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (fp.sha256, _hex(fp.phash, PHASH_BITS), _hex(fp.pdq, PDQ_BITS), fp.pdq_quality, source.image_url,
                 source.article_url, source.title, source.publisher, source.lang,
                 source.reviewed_at.isoformat() if source.reviewed_at else None, source.labels, self._clock().isoformat()),
            )
            return cur.rowcount == 1

    def has_article_image(self, article_url: str, image_url: str) -> bool:
        with closing(self._connect()) as con:
            return con.execute("SELECT 1 FROM items WHERE article_url = ? AND image_url = ?", (article_url, image_url)).fetchone() is not None

    def count(self) -> int:
        with closing(self._connect()) as con:
            return con.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    def nearest(self, fp: Fingerprint, max_distance: int, limit: int) -> list[Match]:
        """Indexed items within max_distance (PDQ) of the image or its mirror, closest first, plus any byte-identical file.
        A linear scan: fine at the size a hackathon index reaches, and the place to add a multi-index hash if it is not."""
        with closing(self._connect()) as con:
            rows = con.execute("SELECT * FROM items").fetchall()
        found: list[Match] = []
        for row in rows:
            stored, stored_phash = int(row["pdq"], 16), int(row["phash"], 16)
            plain, mirror = hamming(fp.pdq, stored), hamming(fp.pdq_flipped, stored)
            distance = min(plain, mirror)
            exact = row["sha256"] == fp.sha256
            if distance > max_distance and not exact:
                continue
            mirrored = mirror < plain
            ph = hamming(fp.phash_flipped if mirrored else fp.phash, stored_phash)
            found.append(Match(
                distance, ph, mirrored, exact,
                Source(row["image_url"], row["article_url"], row["title"], row["publisher"], row["lang"],
                       datetime.fromisoformat(row["reviewed_at"]) if row["reviewed_at"] else None, row["labels"]),
                datetime.fromisoformat(row["retrieved_at"]),
            ))
        return sorted(found, key=lambda m: (m.distance, m.phash_distance))[:limit]
