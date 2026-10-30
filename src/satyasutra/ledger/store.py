"""Append-only ledger storage behind an interface (Bible D5, I6).

SQLite is the first backend because it is real, durable and needs no service. The interface is what the rest of
the system uses, so a Postgres backend can replace it without touching callers.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Protocol

from ..schemas import LedgerEntry
from .chain import GENESIS_HASH, canonical_json, entry_hash, payload_sha256

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    seq            INTEGER PRIMARY KEY,
    prev_hash      TEXT NOT NULL,
    hash           TEXT NOT NULL UNIQUE,
    created_at     TEXT NOT NULL,
    kind           TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    payload        TEXT NOT NULL,
    code_version   TEXT NOT NULL,
    idem_key       TEXT UNIQUE
);
CREATE TRIGGER IF NOT EXISTS entries_no_update BEFORE UPDATE ON entries
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: update refused'); END;
CREATE TRIGGER IF NOT EXISTS entries_no_delete BEFORE DELETE ON entries
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: delete refused'); END;
"""


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    reason: str = ""


class LedgerStore(Protocol):
    def append(self, kind: str, payload: dict, idem_key: str | None = None) -> LedgerEntry: ...
    def get(self, seq: int) -> LedgerEntry | None: ...
    def head(self) -> LedgerEntry | None: ...
    def entries(self) -> Iterator[LedgerEntry]: ...
    def verify(self) -> VerifyResult: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SQLiteLedgerStore:
    def __init__(self, path: str | Path, code_version: str, clock: Callable[[], datetime] = _utc_now):
        self.path = Path(path)
        self.code_version = code_version
        self._clock = clock
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> LedgerEntry:
        return LedgerEntry(
            seq=row["seq"], prev_hash=row["prev_hash"], hash=row["hash"],
            created_at=datetime.fromisoformat(row["created_at"]), kind=row["kind"],
            payload_sha256=row["payload_sha256"], payload=json.loads(row["payload"]),
            code_version=row["code_version"], idem_key=row["idem_key"],
        )

    def append(self, kind: str, payload: dict, idem_key: str | None = None) -> LedgerEntry:
        """Add an entry. A repeated idem_key returns the existing entry and writes nothing (idempotent)."""
        digest = payload_sha256(payload)
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")          # one writer at a time, so seq and prev_hash cannot race
            if idem_key is not None:
                row = con.execute("SELECT * FROM entries WHERE idem_key = ?", (idem_key,)).fetchone()
                if row is not None:
                    con.execute("COMMIT")
                    return self._row_to_entry(row)
            head = con.execute("SELECT seq, hash FROM entries ORDER BY seq DESC LIMIT 1").fetchone()
            seq = 1 if head is None else head["seq"] + 1
            prev = GENESIS_HASH if head is None else head["hash"]
            digest_hash = entry_hash(prev, seq, kind, digest, self.code_version)
            created = self._clock().astimezone(timezone.utc).isoformat()
            con.execute(
                "INSERT INTO entries (seq, prev_hash, hash, created_at, kind, payload_sha256, payload, code_version, idem_key)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (seq, prev, digest_hash, created, kind, digest, canonical_json(payload), self.code_version, idem_key),
            )
            con.execute("COMMIT")
            row = con.execute("SELECT * FROM entries WHERE seq = ?", (seq,)).fetchone()
            return self._row_to_entry(row)
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def get(self, seq: int) -> LedgerEntry | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM entries WHERE seq = ?", (seq,)).fetchone()
        return self._row_to_entry(row) if row else None

    def head(self) -> LedgerEntry | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM entries ORDER BY seq DESC LIMIT 1").fetchone()
        return self._row_to_entry(row) if row else None

    def entries(self) -> Iterator[LedgerEntry]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM entries ORDER BY seq").fetchall()
        for row in rows:
            yield self._row_to_entry(row)

    def verify(self) -> VerifyResult:
        """Re-walk the chain and recompute every hash. One changed byte, a gap or a reorder fails it."""
        expected_seq, prev, checked = 1, GENESIS_HASH, 0
        with self._connect() as con:
            rows = con.execute("SELECT * FROM entries ORDER BY seq").fetchall()
        for row in rows:
            seq = row["seq"]
            if seq != expected_seq:
                return VerifyResult(False, checked, expected_seq, f"sequence gap: expected {expected_seq}, found {seq}")
            if row["prev_hash"] != prev:
                return VerifyResult(False, checked, seq, "prev_hash does not match the previous entry")
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                return VerifyResult(False, checked, seq, "payload is not valid JSON")
            if payload_sha256(payload) != row["payload_sha256"]:
                return VerifyResult(False, checked, seq, "payload no longer matches its recorded hash")
            recomputed = entry_hash(row["prev_hash"], seq, row["kind"], row["payload_sha256"], row["code_version"])
            if recomputed != row["hash"]:
                return VerifyResult(False, checked, seq, "entry hash does not match its fields")
            prev, expected_seq, checked = row["hash"], expected_seq + 1, checked + 1
        return VerifyResult(True, checked)
