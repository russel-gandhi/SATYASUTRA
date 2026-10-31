"""Quiet help page (Raksha RK7): served without caching, loads no shared nav or script from outside, and takes no upload."""

from fastapi.testclient import TestClient

from satyasutra.api.app import create_app
from satyasutra.config import load_config


def test_help_page_is_quiet(tmp_path):
    cfg = load_config().model_copy(deep=True)
    cfg.runtime.report.db_path = str(tmp_path / "records.sqlite3")
    cfg.runtime.provenance.db_path = str(tmp_path / "prov.sqlite3")
    r = TestClient(create_app(cfg)).get("/help")
    assert r.status_code == 200
    assert r.headers["cache-control"] .startswith("no-store")  # a shared phone should not keep the page in its cache
    html = r.text
    assert "nav.js" not in html and 'type="file"' not in html
    assert "about:blank" in html            # the one-tap exit
    assert "/act/plan" in html              # steps come from the plan endpoint, not from text hard-coded in the page
    assert "localStorage" not in html and "sessionStorage" not in html


def test_help_steps_come_from_the_plan_endpoint(tmp_path):
    cfg = load_config().model_copy(deep=True)
    cfg.runtime.report.db_path = str(tmp_path / "records.sqlite3")
    cfg.runtime.provenance.db_path = str(tmp_path / "prov.sqlite3")
    r = TestClient(create_app(cfg)).post("/act/plan", json={"text": "someone is threatening to share my private photos", "labels": [], "ledger_seqs": []})
    body = r.json()
    assert body["intent"] == "intimate_images"
    assert any(x["id"] == "emergency_112" for x in body["routes"])
