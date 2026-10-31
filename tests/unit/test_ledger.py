import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from satyasutra.ledger import GENESIS_HASH, SQLiteLedgerStore, canonical_json, entry_hash, payload_sha256

ROOT = Path(__file__).resolve().parents[2]
FIXED = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version="t1", clock=lambda: FIXED)


def fill(store, n=3):
    return [store.append("verdict", {"n": i, "text": "नमस्ते"}) for i in range(1, n + 1)]


def test_canonical_json_is_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert payload_sha256({"b": 1, "a": 2}) == payload_sha256({"a": 2, "b": 1})


def test_chain_links_and_verifies(store):
    a, b, c = fill(store)
    assert a.prev_hash == GENESIS_HASH and b.prev_hash == a.hash and c.prev_hash == b.hash
    assert (a.seq, b.seq, c.seq) == (1, 2, 3)
    assert a.hash == entry_hash(GENESIS_HASH, 1, "verdict", a.payload_sha256, "t1")
    result = store.verify()
    assert result.ok and result.checked == 3
    assert store.head().seq == 3 and store.get(2).hash == b.hash and store.get(99) is None


def test_empty_ledger_verifies(store):
    assert store.verify().ok and store.head() is None


def test_idempotent_append_returns_existing_entry(store):
    first = store.append("verdict", {"x": 1}, idem_key="same")
    again = store.append("verdict", {"x": 999}, idem_key="same")
    assert again.seq == first.seq == 1 and again.hash == first.hash
    assert len(list(store.entries())) == 1


def test_update_and_delete_are_refused(store):
    fill(store)
    con = sqlite3.connect(store.path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("UPDATE entries SET payload = '{}' WHERE seq = 2")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("DELETE FROM entries WHERE seq = 2")
    con.close()
    assert store.verify().ok


def _tamper(store, sql):
    con = sqlite3.connect(store.path)
    con.isolation_level = None
    con.execute("DROP TRIGGER entries_no_update")
    con.execute("DROP TRIGGER entries_no_delete")
    con.execute(sql)
    con.close()


def test_edited_payload_detected_at_the_right_seq(store):
    fill(store, 4)
    _tamper(store, """UPDATE entries SET payload = '{"n":2,"text":"forged"}' WHERE seq = 2""")
    r = store.verify()
    assert not r.ok and r.first_bad_seq == 2 and "payload" in r.reason and r.checked == 1


def test_edited_payload_with_recomputed_payload_hash_still_fails(store):
    fill(store, 3)
    forged = {"n": 2, "text": "forged"}
    _tamper(store, f"UPDATE entries SET payload = '{canonical_json(forged)}', payload_sha256 = '{payload_sha256(forged)}' WHERE seq = 2")
    r = store.verify()
    assert not r.ok and r.first_bad_seq == 2 and "hash" in r.reason


def test_deleted_middle_entry_detected(store):
    fill(store, 4)
    _tamper(store, "DELETE FROM entries WHERE seq = 2")
    r = store.verify()
    assert not r.ok and r.first_bad_seq == 2 and "gap" in r.reason


def test_changed_kind_or_version_detected(store):
    fill(store, 2)
    _tamper(store, "UPDATE entries SET kind = 'other' WHERE seq = 1")
    assert not store.verify().ok


def test_verify_script_exit_codes(tmp_path):
    db = tmp_path / "l.sqlite3"
    s = SQLiteLedgerStore(db, code_version="t1")
    fill(s)
    script = str(ROOT / "scripts" / "verify_ledger.py")
    ok = subprocess.run([sys.executable, script, "--db", str(db)], capture_output=True, text=True)
    assert ok.returncode == 0 and "intact" in ok.stdout
    _tamper(s, """UPDATE entries SET payload = '{"n":1}' WHERE seq = 1""")
    bad = subprocess.run([sys.executable, script, "--db", str(db)], capture_output=True, text=True)
    assert bad.returncode == 1 and "TAMPERED at seq 1" in bad.stdout
    missing = subprocess.run([sys.executable, script, "--db", str(tmp_path / "none.db")], capture_output=True, text=True)
    assert missing.returncode == 2


def test_reads_and_writes_leave_no_open_connection_so_the_file_can_be_deleted(tmp_path):
    path = tmp_path / "l.sqlite3"
    store = SQLiteLedgerStore(path, code_version="t1", clock=lambda: FIXED)
    fill(store)
    store.get(1), store.head(), list(store.entries()), store.verify()
    path.unlink()                                  # Windows refuses this while any connection is still open


def test_memory_path_is_rejected_clearly():
    with pytest.raises(ValueError, match="memory"):
        SQLiteLedgerStore(":memory:", code_version="t1")


def test_read_only_store_does_not_touch_the_database(tmp_path):
    db = tmp_path / "l.sqlite3"
    fill(SQLiteLedgerStore(db, code_version="t1"), 2)
    con = sqlite3.connect(db)
    con.execute("DROP TRIGGER entries_no_update")
    con.commit()
    con.close()
    ro = SQLiteLedgerStore(db, code_version="t1", read_only=True)
    assert ro.verify().ok
    with pytest.raises(sqlite3.OperationalError):
        ro.append("verdict", {"x": 1})
    con = sqlite3.connect(db)
    triggers = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    con.close()
    assert triggers == {"entries_no_delete"}, "a read-only open must not repair or create anything"


def test_verify_script_rejects_a_database_that_is_not_a_ledger(tmp_path):
    db = tmp_path / "other.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE x (a)")
    con.commit()
    con.close()
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_ledger.py"), "--db", str(db)], capture_output=True, text=True)
    assert r.returncode == 2 and "intact" not in r.stdout
    con = sqlite3.connect(db)
    assert [n for (n,) in con.execute("SELECT name FROM sqlite_master")] == ["x"]
    con.close()
