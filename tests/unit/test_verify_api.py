"""POST /verify with the real Gemini, Fact Check and Bluesky clients pointed at one local server per test.
The server speaks the response shapes captured from the live services (see the other adapter tests)."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from satyasutra import __version__
from satyasutra.api.app import create_app
from satyasutra.cache import TTLCache
from satyasutra.claims import extract_claims
from satyasutra.config import load_config
from satyasutra.fastclock import FastClock
from satyasutra.ledger import SQLiteLedgerStore
from satyasutra.llm import GeminiClient
from satyasutra.sources import FactCheckClient
from satyasutra.sources.bluesky import fetch_post
from satyasutra.verdict_cache import VerdictCache

CONFIG = Path(__file__).resolve().parents[2] / "config"
POST_TEXT = "The new bridge in the capital collapsed this morning killing forty people"
URI = "at://did:plc:abc123/app.bsky.feed.post/3kxyz"


def gemini_reply(items):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps({"items": items})}], "role": "model"}, "finishReason": "STOP"}],
            "usageMetadata": {"totalTokenCount": 120}}


def item(quote, claim=None, kind="factual_claim"):
    return {"quote": quote, "claim_en": claim or quote, "lang": "en", "kind": kind, "checkworthy": True}


def review(claim, rating, publisher="Pub A"):
    return {"claims": [{"text": claim, "claimReview": [{"publisher": {"name": publisher, "site": "x.org"}, "url": f"https://x.org/{publisher}",
                                                        "title": "t", "reviewDate": "2026-10-01T00:00:00Z", "textualRating": rating, "languageCode": "en"}]}]}


@pytest.fixture
def world(tmp_path):
    state = {"delay": 0.0, "gemini": gemini_reply([item(POST_TEXT)]), "gemini_code": 200, "factcheck": {}, "bsky": None, "calls": []}

    class H(BaseHTTPRequestHandler):
        def respond(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            state["calls"].append("gemini")
            time.sleep(state["delay"])
            self.respond(state["gemini_code"], state["gemini"])

        def do_GET(self):
            route = urlparse(self.path).path
            state["calls"].append(route)
            if route.endswith(".getPosts"):
                code, body = state["bsky"]
                return self.respond(code, body)
            self.respond(200, state["factcheck"])

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    cfg = load_config(CONFIG)
    rt = cfg.runtime
    rt = rt.model_copy(update={
        "factcheck": rt.factcheck.model_copy(update={"endpoint": f"{base}/search"}),
        "verify": rt.verify.model_copy(update={"posts_endpoint": f"{base}/xrpc/app.bsky.feed.getPosts"}),
    })
    cfg = cfg.model_copy(update={
        "runtime": rt,
        "policy": cfg.policy.model_copy(update={"gemini": cfg.policy.gemini.model_copy(update={"api_base": f"{base}/v1beta"})}),
    })
    env = {"GEMINI_API_KEY": "g-key", "GOOGLE_FACTCHECK_API_KEY": "f-key"}
    gem = GeminiClient(cfg, env=env, sleep=lambda s: None)
    now = [time.time()]                                   # one controllable clock for every cache in the app
    fc = FactCheckClient(cfg, TTLCache(tmp_path / "c.sqlite3", clock=lambda: now[0]), env=env, clock=lambda: now[0], sleep=lambda s: None)
    store = SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__)
    state["now"], state["cfg"] = now, cfg
    vcache = VerdictCache(TTLCache(tmp_path / "v.sqlite3", clock=lambda: now[0]), cfg, __version__, scope=str(store.path), clock=lambda: now[0])
    state["fastclock"] = FastClock(cfg, TTLCache(tmp_path / "f.sqlite3"))
    app = create_app(cfg, store, extract=lambda p: extract_claims(gem, cfg, p), lookup=fc.search, fetch=lambda u: fetch_post(u, cfg, "salt"), verdict_cache=vcache, fastclock=state["fastclock"])
    yield TestClient(app), store, state
    srv.shutdown()


def test_text_with_no_fact_check_hits_is_unverifiable_and_leaves_a_ledger_row(world):
    client, store, state = world
    body = client.post("/verify", json={"text": POST_TEXT}).json()
    [claim] = body["claims"]
    assert claim["label"] == "UNVERIFIABLE_YET" and claim["confidence_band"] == "low" and claim["citations"] == []
    assert body["extraction"]["status"] == "ok" and claim["ledger_seq"] == 1
    entry = client.get(f"/ledger/{claim['ledger_seq']}").json()
    assert entry["kind"] == "verdict" and entry["payload"]["label"] == "UNVERIFIABLE_YET" and entry["code_version"] == __version__
    assert client.get("/ledger/verify").json()["ok"] is True


def test_a_near_identical_false_review_refutes_and_is_cited_in_the_response_and_the_ledger(world):
    client, store, state = world
    state["factcheck"] = review(POST_TEXT, "False")
    [claim] = client.post("/verify", json={"text": POST_TEXT}).json()["claims"]
    assert claim["label"] == "REFUTED" and [c["role"] for c in claim["citations"]] == ["contradicts"]
    payload = client.get(f"/ledger/{claim['ledger_seq']}").json()["payload"]
    assert payload["evidence"][0]["passage_sha256"] == claim["citations"][0]["passage_sha256"] and payload["reasons"]


def test_a_review_of_another_claim_is_shown_as_related_and_does_not_decide(world):
    client, store, state = world
    state["factcheck"] = review("Bill Gates created the new coronavirus", "False")
    [claim] = client.post("/verify", json={"text": POST_TEXT}).json()["claims"]
    assert claim["label"] == "UNVERIFIABLE_YET" and claim["citations"] == [] and claim["related"][0]["why_not_used"]


def test_repeating_a_request_does_not_add_a_second_ledger_row(world):
    client, store, state = world
    first = client.post("/verify", json={"text": POST_TEXT}).json()["claims"][0]["ledger_seq"]
    second = client.post("/verify", json={"text": POST_TEXT}).json()["claims"][0]["ledger_seq"]
    assert first == second == store.head().seq


def test_a_post_uri_is_fetched_and_checked(world):
    client, store, state = world
    state["bsky"] = (200, {"posts": [{"uri": URI, "author": {"did": "did:plc:abc123", "handle": "h"},
                                      "record": {"text": POST_TEXT, "createdAt": "2026-10-31T10:00:00.000Z", "langs": ["en"]}}]})
    response = client.post("/verify", json={"uri": URI}); assert response.status_code == 200, response.text; body = response.json()
    assert body["post"]["id"] == URI and body["post"]["url"].endswith("/post/3kxyz") and len(body["claims"]) == 1


def test_an_unknown_post_uri_is_404_and_nothing_is_written(world):
    client, store, state = world
    state["bsky"] = (200, {"posts": []})
    assert client.post("/verify", json={"uri": URI}).status_code == 404 and store.head() is None


def test_an_opinion_yields_no_claims_and_no_ledger_row(world):
    client, store, state = world
    state["gemini"] = gemini_reply([item(POST_TEXT, kind="opinion")])
    body = client.post("/verify", json={"text": POST_TEXT}).json()
    assert body["claims"] == [] and body["extraction"]["non_claims"] == 1 and store.head() is None


@pytest.mark.parametrize("payload", [{}, {"text": "a", "uri": "at://x"}, {"text": "   "}, {"uri": "https://example.org/x"}, {"text": "x" * 5000}])
def test_bad_requests_are_422_and_call_nothing(world, payload):
    client, store, state = world
    assert client.post("/verify", json=payload).status_code == 422 and state["calls"] == []


def test_a_model_outage_is_502_not_an_invented_verdict(world):
    client, store, state = world
    state["gemini_code"], state["gemini"] = 500, {"error": {"message": "boom"}}
    response = client.post("/verify", json={"text": POST_TEXT})
    assert response.status_code == 502 and store.head() is None and "g-key" not in response.text


def test_a_missing_key_is_503_with_the_variable_name_not_a_crash(tmp_path):
    cfg = load_config(CONFIG)
    store = SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__)
    gem = GeminiClient(cfg, env={})
    app = create_app(cfg, store, extract=lambda p: extract_claims(gem, cfg, p), lookup=lambda t, l: None, verdict_cache=VerdictCache(TTLCache(tmp_path / "v.sqlite3"), cfg, __version__))
    response = TestClient(app).post("/verify", json={"text": POST_TEXT})
    assert response.status_code == 503 and "GEMINI_API_KEY" in response.json()["detail"]
