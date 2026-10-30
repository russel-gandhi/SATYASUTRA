"""The whole path live: a post taken from the Bluesky stream in this run, real Gemini, real Fact Check, a real ledger.
Run with `pytest -m contract`."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from satyasutra import __version__
from satyasutra.api.app import create_app
from satyasutra.config import load_config
from satyasutra.envfile import load_env_file
from satyasutra.ingest import IngestStats, stream_posts
from satyasutra.ledger import SQLiteLedgerStore

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]
WAIT_S = 90
POSTS_TRIED = 3          # a post may hold no checkable claim; try a few before saying the path produced none
CHECKABLE = "Smoking causes lung cancer"


def test_a_live_post_goes_through_every_stage_and_the_ledger_stays_valid(tmp_path):
    load_env_file(ROOT / ".env")
    cfg = load_config()
    store = SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__)
    client = TestClient(create_app(cfg, store))

    async def posts():
        found = []
        async for post in stream_posts(cfg.runtime.ingest, IngestStats(), "contract-test-salt"):
            found.append(post)
            if len(found) == POSTS_TRIED:
                return found

    live = asyncio.run(asyncio.wait_for(posts(), WAIT_S))
    bodies = []
    for post in live:
        response = client.post("/verify", json={"text": post.text})
        assert response.status_code == 200, response.text
        bodies.append(response.json())

    for body in bodies:
        assert body["extraction"]["status"] in ("ok", "no_items"), body["extraction"]
        for claim in body["claims"]:
            assert claim["label"] in ("UNVERIFIABLE_YET", "SUPPORTED", "REFUTED", "MISSING_CONTEXT")
            if claim["label"] != "UNVERIFIABLE_YET":
                assert claim["citations"], "a decided label must cite evidence"
            entry = client.get(f"/ledger/{claim['ledger_seq']}").json()
            assert entry["payload"]["label"] == claim["label"]
    assert client.get("/ledger/verify").json()["ok"] is True


def test_a_checkable_statement_is_extracted_looked_up_and_decided_or_left_unverifiable(tmp_path):
    load_env_file(ROOT / ".env")
    cfg = load_config()
    client = TestClient(create_app(cfg, SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__)))
    response = client.post("/verify", json={"text": CHECKABLE})
    assert response.status_code == 200, response.text
    [claim] = response.json()["claims"]
    assert claim["label"] != "REFUTED"                       # a true claim; the API returns reviews of its opposite (F1-07)
    assert claim["ledger_seq"] >= 1


def test_a_live_post_uri_is_fetched_from_the_appview_and_checked(tmp_path):
    load_env_file(ROOT / ".env")
    cfg = load_config()
    client = TestClient(create_app(cfg, SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__)))

    async def first():
        async for post in stream_posts(cfg.runtime.ingest, IngestStats(), "contract-test-salt"):
            return post

    post = asyncio.run(asyncio.wait_for(first(), WAIT_S))
    response = client.post("/verify", json={"uri": post.id})
    if response.status_code == 404:
        pytest.skip("the AppView had not indexed this just-created post yet")      # reported as skipped, not as a pass
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["post"]["id"] == post.id and body["extraction"]["status"] in ("ok", "no_items")
