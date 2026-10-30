"""A small SQLite cache with a time-to-live. It stores what a source really returned and when, so a cached answer is never
passed off as a fresh one: callers get the original fetch time back."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Callable


class TTLCache:
    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        with self._connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, fetched_at REAL NOT NULL, payload TEXT NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def get(self, key: str, ttl_s: float) -> tuple[str, float] | None:
        """(payload, fetched_at epoch seconds) when present and younger than ttl_s, else None."""
        with self._connect() as con:
            row = con.execute("SELECT payload, fetched_at FROM cache WHERE key = ?", (key,)).fetchone()
        if row is None or self._clock() - row[1] > ttl_s:
            return None
        return row[0], row[1]

    def put(self, key: str, payload: str, at: float | None = None) -> float:
        now = self._clock() if at is None else at
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO cache (key, fetched_at, payload) VALUES (?, ?, ?)", (key, now, payload))
        return now
