import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from satyasutra import __version__
from satyasutra.api.app import create_app
from satyasutra.config import load_config
from satyasutra.ledger import SQLiteLedgerStore

CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture
def ctx(tmp_path):
    cfg = load_config(CONFIG)
    store = SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__)
    return TestClient(create_app(cfg, store)), store, cfg


def test_health_reports_version_and_config_checksum(ctx):
    client, store, cfg = ctx
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["version"] == __version__
    assert body["config_checksum"] == cfg.checksum and body["ledger_entries"] == 0
    store.append("verdict", {"a": 1})
    assert client.get("/health").json()["ledger_entries"] == 1


def test_ledger_entry_and_404(ctx):
    client, store, _ = ctx
    entry = store.append("verdict", {"a": 1})
    got = client.get("/ledger/1").json()
    assert got["hash"] == entry.hash and got["payload"] == {"a": 1}
    assert client.get("/ledger/2").status_code == 404


def test_verify_route_is_not_shadowed_by_seq_route(ctx):
    client, store, _ = ctx
    store.append("verdict", {"a": 1})
    body = client.get("/ledger/verify").json()
    assert body == {"ok": True, "checked": 1, "first_bad_seq": None, "reason": ""}


def test_verify_route_reports_tampering(ctx):
    client, store, _ = ctx
    store.append("verdict", {"a": 1})
    con = sqlite3.connect(store.path)
    con.isolation_level = None
    con.execute("DROP TRIGGER entries_no_update")
    con.execute("""UPDATE entries SET payload = '{"a":2}' WHERE seq = 1""")
    con.close()
    body = client.get("/ledger/verify").json()
    assert body["ok"] is False and body["first_bad_seq"] == 1
