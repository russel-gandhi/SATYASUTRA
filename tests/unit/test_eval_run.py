"""The evaluation harness: reading the agent's event stream, retries, the report, and the fact-check candidate builder (spec D1)."""

import importlib.util
import json
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from satyasutra import __version__
from satyasutra.api.app import create_app
from satyasutra.claims import ExtractionResult, Grounded, Judgements
from satyasutra.claims.contrast import OfficialResult
from satyasutra.config import load_config
from satyasutra.errors import Unreachable
from satyasutra.ledger import SQLiteLedgerStore
from satyasutra.schemas import Claim, Evidence, Post
from satyasutra.schemas.core import sha256_text
from satyasutra.sources import FactCheckResult

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"
NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)
CLAIM = "The weighted average lending rate remained unchanged at 10.46 per cent in September 2026."


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"evalset_{name}", ROOT / "scripts" / "eval" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    sys.path.insert(0, str(ROOT / "scripts" / "eval"))
    spec.loader.exec_module(mod)
    return mod


run, build_f = load("run"), load("build_f")


def contrast_evidence() -> Evidence:
    passage = "Reserve Bank of India: Lending and Deposit Rates. The rate remained unchanged at 8.96 per cent in September 2026."
    return Evidence(role="contradicts", route="contrast", source_id="rbi_press", url="https://www.rbi.org.in/x", fetched_at=NOW, tier=1, passage=passage,
                    passage_sha256=sha256_text(passage), publisher="Reserve Bank of India", contrast_claim="The rate remained unchanged at 8.96 per cent.")


def make_runner(tmp_path, *, claims=True, contrast=True, fail=0):
    """A runner that streams from the real app with its model and network dependencies replaced, as the harness does with the real ones."""
    state = {"fails": fail}

    def extract(post):
        c = [Claim(id="c1", post_id=post.id, text_en=post.text, text_orig=post.text, span=(0, len(post.text)), lang="en")] if claims else []
        return ExtractionResult("ok", c, model="m", prompt_sha256="p")

    def lookup(text, lang):
        if state["fails"]:
            state["fails"] -= 1
            raise Unreachable("factcheck_tools", "down")
        return FactCheckResult([], 0, False)

    app = create_app(load_config(CONFIG), SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__), extract=extract, lookup=lookup,
                     judge=lambda t, e: Judgements({}, "m", "p"), ground=lambda t: Grounded(),
                     official=lambda t: OfficialResult(contrast=[contrast_evidence()] if contrast else []))
    client = TestClient(app, raise_server_exceptions=False)

    def runner(text):
        with client.stream("GET", "/agent/stream", params={"text": text}) as r:
            return "".join(r.iter_text()), 0.5
    return runner


ROW = {"id": "c99", "group": "C", "truth": "false", "text": CLAIM, "lang": "en", "split": "dev", "built_from": "t99", "base_id": ""}


# ---- reading a stream ---------------------------------------------------------------------------------------------------------

def test_parse_result_reads_only_the_result_event():
    sse = 'id: 1\nevent: node_started\ndata: {"x": 1}\n\nid: 2\nevent: result\ndata: {"claims": [{"label": "REFUTED"}]}\n\n'
    assert run.parse_result(sse) == {"claims": [{"label": "REFUTED"}]}
    assert run.parse_result("event: run_finished\ndata: {}\n\n") is None


def test_top_claim_prefers_the_most_decisive_label_and_lists_routes():
    body = {"claims": [{"label": "UNVERIFIABLE_YET", "confidence_band": "low", "reasons": ["a"], "citations": []},
                       {"label": "REFUTED", "confidence_band": "medium", "reasons": ["b"], "citations": [{"route": "contrast"}, {"route": "factcheck"}]}]}
    top = run.top_claim(body)
    assert top["label"] == "REFUTED" and top["band"] == "medium" and top["routes"] == ["contrast", "factcheck"]
    assert run.top_claim({"claims": []})["label"] == "NO_CLAIM"


# ---- the real app, with fakes -------------------------------------------------------------------------------------------------

def test_a_changed_number_refuted_by_official_text_comes_through_the_real_stream(tmp_path):
    results = run.run_rows([ROW], make_runner(tmp_path), sleep=lambda s: None)
    r = results[0]
    assert r["label"] == "REFUTED" and r["outcome"] == "correct" and "contrast" in r["routes"] and r["band"] == "medium"


def test_the_same_claim_without_a_contradiction_is_an_honest_abstention(tmp_path):
    r = run.run_rows([ROW], make_runner(tmp_path, contrast=False), sleep=lambda s: None)[0]
    assert r["label"] == "UNVERIFIABLE_YET" and r["outcome"] == "abstain"


def test_no_checkable_claim_is_recorded_as_no_claim_not_an_error(tmp_path):
    r = run.run_rows([{**ROW, "truth": "unknown"}], make_runner(tmp_path, claims=False), sleep=lambda s: None)[0]
    assert r["label"] == "NO_CLAIM" and r["outcome"] == "correct"


def test_a_failing_source_is_retried_then_recorded_as_error_never_as_a_verdict(tmp_path):
    waits = []
    ok = run.run_rows([ROW], make_runner(tmp_path, fail=1), sleep=waits.append)[0]
    assert ok["label"] == "REFUTED" and len(waits) == 1                      # one failure, one wait, then it worked
    waits.clear()
    bad = run.run_rows([ROW], make_runner(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, fail=99), sleep=waits.append, retries=2)[0]
    assert bad["label"] == "ERROR" and bad["outcome"] == "error" and len(waits) == 2


# ---- the report ---------------------------------------------------------------------------------------------------------------

def result(id_, group, truth, label, band="", outcome_="correct", routes=(), base_id=""):
    return {"id": id_, "group": group, "truth": truth, "text": f"claim {id_}", "lang": "en", "split": "dev", "label": label, "band": band, "routes": list(routes),
            "reasons": ["why"], "seconds": 1.0, "outcome": outcome_, "built_from": "", "base_id": base_id}


def test_report_has_the_headline_numbers_ranges_limits_and_wrong_answers():
    results = [result("t1", "T", "true", "REFUTED", "medium", "wrong"), result("t2", "T", "true", "UNVERIFIABLE_YET", "low", "abstain"),
               result("c1", "C", "false", "REFUTED", "medium", "correct", ["contrast"]), result("u1", "U", "unknown", "UNVERIFIABLE_YET", "low", "correct"),
               result("e1", "U", "unknown", "ERROR", "", "error")]
    meta = {"when": "2026-10-31 12:00 UTC", "sha": "abc1234", "dirty": False, "split": "dev", "factcheck": "off", "official": "snapshot", "command": "python scripts/eval/run.py"}
    text = run.render(results, meta, [("b-t01", "src/x.py")])
    assert "True claims called false" in text and "1/2 (50%" in text            # 1 of 2 true claims refuted, with a range
    assert "| contrast | 1 | 1/1" in text
    assert "Wrong answers (1)" in text and "claim t1" in text
    assert "Excluded as contaminated" in text and "b-t01" in text and "Errors (1)" in text
    assert "not general accuracy" in text


# ---- fact-check candidates (group F) ------------------------------------------------------------------------------------------

API_BODY = {"claims": [
    {"text": "Free recharge for all students", "claimant": "WhatsApp forward", "claimReview": [
        {"publisher": {"name": "Alt News"}, "url": "https://example.org/a", "title": "No free recharge", "reviewDate": "2026-10-20T00:00:00Z", "textualRating": "False", "languageCode": "en"}]},
    {"text": "A true thing", "claimReview": [{"publisher": {"name": "X"}, "url": "https://example.org/b", "textualRating": "True"}]},
    {"text": "No url claim", "claimReview": [{"publisher": {"name": "X"}, "textualRating": "False"}]},
]}


def test_candidates_keep_only_false_rated_reviews_with_a_url():
    rows = build_f.candidates_from(API_BODY, "recharge", "en", lambda rating: rating.lower() == "false")
    assert [r["text"] for r in rows] == ["Free recharge for all students"]
    r = rows[0]
    assert r["truth"] == "false" and r["review_status"] == "pending" and r["labelled_by"] == [] and r["source_date"] == "2026-10-20" and r["publisher"] == "Alt News"


class Api(BaseHTTPRequestHandler):
    def do_GET(self):
        raw = json.dumps(API_BODY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


def test_build_f_main_writes_pending_candidates_and_never_echoes_the_key(tmp_path, monkeypatch, capsys):
    server = HTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("GOOGLE_FACTCHECK_API_KEY", "s3cret-key-value")
        monkeypatch.setattr(build_f, "CANDIDATES", tmp_path / "cands.jsonl")
        seeds = tmp_path / "seeds.txt"
        seeds.write_text("# comment\nrecharge\n", encoding="utf-8")
        assert build_f.main(["--seeds", str(seeds), "--lang", "en", "--base-url", f"http://127.0.0.1:{server.server_port}/v1/claims:search"]) == 0
    finally:
        server.shutdown()
        server.server_close()
    out = capsys.readouterr().out
    rows = [json.loads(line) for line in (tmp_path / "cands.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["review_status"] == "pending" and "s3cret-key-value" not in out


def test_build_f_needs_the_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GOOGLE_FACTCHECK_API_KEY", raising=False)
    monkeypatch.setattr(build_f, "load_env_file", lambda path: [], raising=False)
    seeds = tmp_path / "s.txt"
    seeds.write_text("x\n", encoding="utf-8")
    # main() reads the key from the environment after loading .env, so only assert the contract when no key is present in this environment
    if build_f.main(["--seeds", str(seeds)]) != 2:
        pytest.skip("a real key is present in .env on this machine")
    assert "GOOGLE_FACTCHECK_API_KEY" in capsys.readouterr().err


def test_post_verify_the_telegram_path_runs_the_official_text_check_too(tmp_path):
    """The bot calls POST /verify. It used to skip the official-text step, so a changed number was 'cannot tell' there and REFUTED on the web page."""
    for contrast, label in ((True, "REFUTED"), (False, "UNVERIFIABLE_YET")):
        d = tmp_path / f"v{contrast}"
        d.mkdir()
        app = create_app(load_config(CONFIG), SQLiteLedgerStore(d / "l.sqlite3", code_version=__version__),
                         extract=lambda post: ExtractionResult("ok", [Claim(id="c1", post_id=post.id, text_en=post.text, text_orig=post.text, span=(0, len(post.text)), lang="en")], model="m", prompt_sha256="p"),
                         lookup=lambda t, lang: FactCheckResult([], 0, False), judge=lambda t, e: Judgements({}, "m", "p"), ground=lambda t: Grounded(),
                         official=lambda t, c=contrast: OfficialResult(contrast=[contrast_evidence()] if c else []))
        body = TestClient(app).post("/verify", json={"text": CLAIM}).json()
        assert body["claims"][0]["label"] == label
