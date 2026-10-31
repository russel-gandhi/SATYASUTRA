"""The UI is a single static page served at the API root."""

from pathlib import Path

from fastapi.testclient import TestClient

from satyasutra import __version__
from satyasutra.api.app import create_app
from satyasutra.config import load_config
from satyasutra.ledger import SQLiteLedgerStore

CONFIG = Path(__file__).resolve().parents[2] / "config"


def test_root_serves_the_page_and_it_only_calls_routes_that_exist(tmp_path):
    cfg = load_config(CONFIG)
    app = create_app(cfg, SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__))
    page = TestClient(app).get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"] and "SATYASUTRA" in page.text
    routes = {r.path for r in app.routes}
    for called in ("/verify", "/health", "/ledger/verify", "/fastclock/top", "/fastclock/stats", "/media/check", "/agent/stream"):
        assert called in routes and called in page.text
