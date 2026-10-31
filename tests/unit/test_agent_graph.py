"""The claim agent graph: node order, tool reports, early stop, failure reporting, and the SSE endpoint that serves it."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from satyasutra import __version__
from satyasutra.agent import trace_events
from satyasutra.agent.graph import AgentRun, build_claim_agent
from satyasutra.api.app import create_app
from satyasutra.claims import ExtractionResult, Grounded, Judgements
from satyasutra.config import load_config
from satyasutra.errors import Unreachable
from satyasutra.ledger import SQLiteLedgerStore
from satyasutra.schemas import Claim, Evidence, Post
from satyasutra.schemas.core import sha256_text
from satyasutra.sources import Entity, FactCheckResult

CONFIG = Path(__file__).resolve().parents[2] / "config"
NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)
TEXT = "COVID-19 vaccines contain microchips that track people"


def make_post(text):
    return Post(id="api_text:1", source_id="api_text", url="", author_hash="h" * 64, text=text, posted_at=NOW, fetched_at=NOW)


def claim():
    return Claim(id="c1", post_id="api_text:1", text_en=TEXT, text_orig=TEXT, span=(0, len(TEXT)), lang="en")


def review():
    reviewed = "Vaccines have tracking chips inside"
    passage = f'FactCheck.org rated the claim "{reviewed}" as "False".'
    return Evidence(role="context", route="factcheck", source_id="factcheck_tools", url="https://x.org/r", fetched_at=NOW, tier=2, passage=passage,
                    passage_sha256=sha256_text(passage), publisher="FactCheck.org", rating="False", reviewed_claim=reviewed, reviewed_at=NOW)


class Deps:
    def __init__(self, claims=True, extraction_status="ok", relation="same", fail_lookup=False):
        self.claims, self.status, self.relation, self.fail_lookup = [claim()] if claims else [], extraction_status, relation, fail_lookup
        self.ev = review()
        self.calls = []

    def extract(self, post):
        self.calls.append("extract")
        return ExtractionResult(self.status, self.claims, reason="boom" if self.status == "failed" else "", model="m", prompt_sha256="p")

    def lookup(self, text, lang):
        self.calls.append("lookup")
        if self.fail_lookup:
            raise Unreachable("factcheck_tools", "timed out")
        return FactCheckResult([self.ev], 0, False)

    def judge(self, text, evidence):
        self.calls.append("judge")
        return Judgements({self.ev.passage_sha256: self.relation}, "m", "p")

    def ground(self, text):
        self.calls.append("ground")
        return Grounded([Entity("COVID-19", "Q84263196", "COVID-19", "disease", "https://www.wikidata.org/wiki/Q84263196")])


def run_graph(tmp_path, deps):
    cfg = load_config(CONFIG)
    ledger = SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__)
    run = AgentRun()
    graph = build_claim_agent(cfg, ledger, make_post, deps.extract, deps.lookup, deps.judge, deps.ground, run)

    async def go():
        return [e async for e in trace_events(graph, {"job": "j1", "text": TEXT}, "j1", cfg.runtime.trace)]

    return asyncio.run(go()), run, ledger


def test_a_claim_runs_every_step_reports_each_tool_and_ends_with_a_cited_refutation(tmp_path):
    deps = Deps()
    events, run, ledger = run_graph(tmp_path, deps)
    started = [e.node for e in events if e.kind == "node_started"]
    assert started[:2] == ["read_post", "extract"] and set(started[2:4]) == {"registry", "ground"} and started[4:] == ["judge", "decide"]
    assert events[-1].kind == "run_finished" and [e.seq for e in events] == list(range(1, len(events) + 1))
    tools = [e.data["name"] for e in events if e.kind == "tool_call"]
    assert tools.count("fact-check registry") == 1 and "gemini: same claim?" in tools and "wikidata: ground entities" in tools and "rules + ledger" in tools
    result = run.outcome.results[0]
    assert result.outcome.verdict.label == "REFUTED" and result.ledger_seq == 1 and ledger.head().seq == 1
    assert [e.id for e in run.grounded["c1"].entities] == ["Q84263196"]
    assert "ledger entry #1" in next(e.data["summary"] for e in events if e.kind == "tool_call" and e.data["name"] == "rules + ledger")


def test_an_opposite_review_does_not_refute(tmp_path):
    _, run, _ = run_graph(tmp_path, Deps(relation="opposite"))
    assert run.outcome.results[0].outcome.verdict.label == "UNVERIFIABLE_YET"


def test_no_checkable_claim_stops_after_extraction_and_calls_nothing_else(tmp_path):
    deps = Deps(claims=False)
    events, run, ledger = run_graph(tmp_path, deps)
    assert [e.node for e in events if e.kind == "node_started"] == ["read_post", "extract"]
    assert deps.calls == ["extract"] and run.outcome is None and ledger.head() is None and events[-1].kind == "run_finished"


def test_a_failed_extraction_is_an_error_event_with_the_class_only_and_writes_nothing(tmp_path):
    events, run, ledger = run_graph(tmp_path, Deps(extraction_status="failed"))
    assert events[-1].kind == "error" and events[-1].data["type"] == "ExtractionFailed" and events[-1].node == "extract"
    assert "boom" not in events[-1].model_dump_json() and run.outcome is None and ledger.head() is None


def test_a_registry_outage_is_an_error_event_not_a_guess(tmp_path):
    events, run, ledger = run_graph(tmp_path, Deps(fail_lookup=True))
    assert events[-1].kind == "error" and events[-1].data["type"] == "Unreachable" and run.outcome is None and ledger.head() is None


def sse(client, text):
    out, kind = [], None
    with client.stream("GET", "/agent/stream", params={"text": text}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                out.append((kind, json.loads(line[6:])))
    return out


def test_the_endpoint_streams_the_trace_then_a_result_with_verdicts_and_entities(tmp_path):
    deps = Deps()
    app = create_app(load_config(CONFIG), SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__),
                     extract=deps.extract, lookup=deps.lookup, judge=deps.judge, ground=deps.ground)
    events = sse(TestClient(app), TEXT)
    kinds = [k for k, _ in events]
    assert kinds[0] == "node_started" and kinds[-2:] == ["run_finished", "result"] and "tool_call" in kinds
    body = events[-1][1]
    claim_body = body["claims"][0]
    assert claim_body["label"] == "REFUTED" and claim_body["ledger_seq"] == 1 and claim_body["entities"][0]["id"] == "Q84263196"
    assert claim_body["citations"][0]["publisher"] == "FactCheck.org" and app.state.active_traces == 0


def test_the_endpoint_sends_no_result_when_the_run_fails_and_refuses_blank_text(tmp_path):
    deps = Deps(extraction_status="failed")
    app = create_app(load_config(CONFIG), SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__), extract=deps.extract, lookup=deps.lookup)
    client = TestClient(app)
    kinds = [k for k, _ in sse(client, TEXT)]
    assert kinds[-1] == "error" and "result" not in kinds
    assert client.get("/agent/stream", params={"text": "   "}).status_code == 422
    assert client.get("/agent/stream").status_code == 422
