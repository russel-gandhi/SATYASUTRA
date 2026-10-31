"""Official text and contrast refutation: the gate, the index, the number comparison, and the rule that turns a contradiction into REFUTED.

Feed bodies have the shape captured from the live RBI and SEBI feeds on 2026-10-31 (RBI: HTML in CDATA descriptions, SEBI: title only).
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from satyasutra import __version__
from satyasutra.agent import trace_events
from satyasutra.agent.graph import AgentRun, build_claim_agent
from satyasutra.claims import ExtractionResult, OfficialResult
from satyasutra.claims.contrast import contrast_check, parse_number
from satyasutra.claims.rules import decide
from satyasutra.config import load_config
from satyasutra.errors import Unreachable
from satyasutra.ledger import SQLiteLedgerStore
from satyasutra.schemas import Claim, Post
from satyasutra.schemas.core import sha256_text
from satyasutra.sources import FactCheckResult
from satyasutra.sources.index import OfficialIndex, Passage
from satyasutra.sources.official import host_allowed, parse_feed, parse_time, passage_evidence, refresh, refresh_if_stale

CONFIG = Path(__file__).resolve().parents[2] / "config"
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
RBI = b"""\xef\xbb\xbf<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>PRESS RELEASES FROM RBI</title>
<item><title><![CDATA[Repo rate unchanged at 5.5 per cent by the Monetary Policy Committee]]></title>
<description><![CDATA[<table><tr><td><p>The Monetary Policy Committee decided to keep the policy repo rate at <b>5.5</b> per cent &amp; the stance neutral.</p></td></tr></table>]]></description>
<link>https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=1</link><pubDate>Fri, 30 Oct 2026 10:00:00 +0530</pubDate></item>
<item><title>Off-site item</title><description>x</description><link>https://evil.example.com/a</link></item>
<item><title>No link here</title><description>x</description></item></channel></rss>"""
SEBI = b"""<?xml version="1.0" encoding="UTF-8" standalone="no"?><rss version="2.0"><channel><title>SEBI RSS Feed</title>
<item><title>Extension of timeline for compliance of Digital Accessibility Circulars</title><description>Extension of timeline for compliance of Digital Accessibility Circulars</description>
<link>https://www.sebi.gov.in/legal/circulars/oct-2026/extension_105523.html</link><pubDate>30 Oct, 2026 +0530</pubDate></item></channel></rss>"""
CLAIM = "RBI has raised the repo rate to 6.5 per cent"


def cfg():
    return load_config(CONFIG)


def feed_of(cfg_, fid):
    return next(f for f in cfg_.runtime.official.feeds if f.id == fid)


def test_parse_feed_strips_html_keeps_the_gate_and_reads_both_date_styles():
    c = cfg()
    items = parse_feed(RBI, feed_of(c, "rbi_press"), c, NOW)
    assert len(items) == 1                                                   # off-site and link-less items are skipped
    p = items[0]
    assert "<" not in p.text and "5.5 per cent & the stance neutral" in p.text and p.text.startswith("Reserve Bank of India: Repo rate unchanged")
    assert p.published_at == datetime(2026, 10, 30, 4, 30, tzinfo=timezone.utc) and p.tier == 1
    sebi = parse_feed(SEBI, feed_of(c, "sebi_feed"), c, NOW)
    assert sebi[0].published_at == datetime(2026, 10, 29, 18, 30, tzinfo=timezone.utc)
    assert parse_time("not a date") is None and parse_time("") is None
    assert host_allowed("https://www.rbi.org.in/x", c.policy.allowed_domains) and not host_allowed("https://rbi.org.in.evil.com/x", c.policy.allowed_domains)


def test_the_index_finds_passages_ranks_them_and_treats_query_text_as_plain_words(tmp_path):
    c = cfg()
    idx = OfficialIndex(tmp_path / "o.sqlite3")
    for p in parse_feed(RBI, feed_of(c, "rbi_press"), c, NOW) + parse_feed(SEBI, feed_of(c, "sebi_feed"), c, NOW):
        assert idx.add(p) and not idx.add(p)                                 # the same text is stored once
    assert idx.count() == 2
    hit = idx.search(CLAIM, 5, 3)
    assert hit and "repo rate" in hit[0].text and hit[0].source_id == "rbi_press"
    assert idx.search('repo" OR NEAR(x) AND * -rate', 5, 3)                  # FTS5 syntax in the query is just words
    assert idx.search("xx", 5, 3) == [] and idx.search("zebra quantum", 5, 3) == []


def test_refresh_reports_each_feed_by_name_and_never_raises(tmp_path):
    c = cfg()

    def handler(request):
        if "rbi" in request.url.host:
            return httpx.Response(200, content=RBI)
        return httpx.Response(403, content=b"blocked")

    idx = OfficialIndex(tmp_path / "o.sqlite3")
    status = refresh(c, idx, httpx.Client(transport=httpx.MockTransport(handler)), NOW)
    assert status["rbi_press"] == "ok: 1 new of 1" and status["sebi_feed"] == "HTTP 403"

    def broken(request):
        return httpx.Response(200, content=b"<rss><item>")

    def down(request):
        raise httpx.ConnectError("failed at https://www.rbi.org.in/x?key=SECRET")

    bad = refresh(c, OfficialIndex(tmp_path / "b.sqlite3"), httpx.Client(transport=httpx.MockTransport(broken)), NOW)
    gone = refresh(c, OfficialIndex(tmp_path / "d.sqlite3"), httpx.Client(transport=httpx.MockTransport(down)), NOW)
    assert bad["rbi_press"] == "ParseError" and gone["rbi_press"] == "ConnectError" and "SECRET" not in repr(gone)


def test_a_feed_outside_the_allowed_domains_is_never_fetched(tmp_path):
    c = cfg()
    off = c.runtime.official.model_copy(update={"feeds": [c.runtime.official.feeds[0].model_copy(update={"url": "https://evil.example.com/feed.xml"})]})
    c = c.model_copy(update={"runtime": c.runtime.model_copy(update={"official": off})})
    seen = []
    status = refresh(c, OfficialIndex(tmp_path / "o.sqlite3"), httpx.Client(transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200))), NOW)
    assert seen == [] and status["rbi_press"] == "blocked by the allowed-domains gate"


def test_refresh_if_stale_only_fetches_when_the_index_is_old(tmp_path):
    c = cfg()
    idx = OfficialIndex(tmp_path / "o.sqlite3")
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(200, content=RBI if "rbi" in request.url.host else SEBI)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert refresh_if_stale(c, idx, http, NOW) is not None and len(calls) == 2
    assert refresh_if_stale(c, idx, http, NOW) is None and len(calls) == 2
    later = datetime.fromtimestamp(NOW.timestamp() + c.runtime.official.refresh_s + 1, timezone.utc)
    assert refresh_if_stale(c, idx, http, later) is not None and len(calls) == 4


@pytest.mark.parametrize("text,value", [("6.5 per cent", 6.5), ("₹2,000", 2000.0), ("Rs. 5 lakh", 500000.0), ("2 crore rupees", 2e7), ("6.5%", 6.5),
                                        ("5 to 6", None), ("none", None), ("", None)])
def test_parse_number(text, value):
    assert parse_number(text) == value


class FakeGemini:
    model = "fake-model"

    def __init__(self, items=None, error=None):
        self.items, self.error = items, error

    def generate_json(self, prompt, schema, system=None):
        if self.error:
            raise self.error
        return SimpleNamespace(data={"items": self.items})


def passage(text="Reserve Bank of India: Repo rate unchanged at 5.5 per cent. The Monetary Policy Committee decided to keep the policy repo rate at 5.5 per cent."):
    return Passage("rbi_press", "Reserve Bank of India", 1, "https://www.rbi.org.in/x", "t", text, sha256_text(text), NOW, NOW)


def item(**kw):
    return {"passage": 1, "same_subject": True, "same_quantity": True, "claim_value": "6.5 per cent", "official_value": "5.5 per cent"} | kw


def test_a_different_number_for_the_same_quantity_fires_and_carries_the_official_sentence():
    out = contrast_check(FakeGemini([item()]), cfg(), CLAIM, [passage()])
    assert len(out.contrast) == 1 and out.error == ""
    ev = out.contrast[0]
    assert ev.route == "contrast" and ev.role == "contradicts" and ev.tier == 1 and ev.publisher == "Reserve Bank of India"
    assert "5.5 per cent" in ev.contrast_claim and out.checked[0]["fired"] is True and "6.5 per cent" in out.checked[0]["why"]


@pytest.mark.parametrize("change,why", [
    ({"claim_value": "5.5 per cent", "official_value": "5.5 per cent"}, "numbers match"),
    ({"same_quantity": False}, "not the same subject and quantity"),
    ({"same_subject": False}, "not the same subject and quantity"),
    ({"claim_value": "7.5 per cent"}, "not in the text it came from"),                             # invented: the claim never says 7.5
    ({"official_value": "4.5 per cent"}, "not in the text it came from"),                          # invented: the passage never says 4.5
    ({"claim_value": "", "official_value": ""}, "not in the text it came from"),
])
def test_nothing_fires_on_a_match_a_missing_quantity_or_an_invented_span(change, why):
    out = contrast_check(FakeGemini([item(**change)]), cfg(), "RBI has raised the repo rate to 6.5 per cent but 5.5 per cent was the old one", [passage()])
    assert out.contrast == []
    assert out.checked[0]["why"].endswith(why) or why in out.checked[0]["why"]


def test_equal_numbers_do_not_fire():
    out = contrast_check(FakeGemini([item(claim_value="5.5 per cent")]), cfg(), "The repo rate is 5.5 per cent", [passage()])
    assert out.contrast == [] and out.checked[0]["why"] == "the numbers match"


def test_a_failed_or_malformed_model_answer_gives_no_evidence_and_names_the_failure():
    assert contrast_check(FakeGemini(error=Unreachable("gemini_llm", "x")), cfg(), CLAIM, [passage()]).error == "Unreachable"
    bad = SimpleNamespace(generate_json=lambda *a, **k: SimpleNamespace(data={"items": "nope"}), model="m")
    assert contrast_check(bad, cfg(), CLAIM, [passage()]).error == "BadOutput"
    assert contrast_check(FakeGemini([]), cfg(), CLAIM, []).contrast == []


def claim():
    return Claim(id="c1", post_id="p", text_en=CLAIM, text_orig=CLAIM, span=(0, len(CLAIM)), lang="en")


def test_the_rules_turn_a_tier_1_contradiction_into_refuted_with_medium_confidence_and_the_official_citation():
    ev = contrast_check(FakeGemini([item()]), cfg(), CLAIM, [passage()]).contrast
    out = decide(claim(), ev, cfg())
    assert out.verdict.label == "REFUTED" and out.verdict.confidence_band == "medium" and "COERCED" not in out.verdict.flags
    assert out.verdict.evidence[0].route == "contrast" and any("gives a different number" in r for r in out.reasons)
    low = ev[0].model_copy(update={"tier": 3})
    assert decide(claim(), [low], cfg()).verdict.label == "UNVERIFIABLE_YET"                  # only tier 1 or 2 decides
    assert decide(claim(), [passage_evidence(passage())], cfg()).verdict.label == "UNVERIFIABLE_YET"   # a retrieved passage alone decides nothing


def test_the_agent_runs_the_official_step_in_parallel_and_the_contradiction_decides_the_verdict(tmp_path):
    c = cfg()
    ledger = SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__)
    post = lambda text: Post(id="api_text:1", source_id="api_text", url="", author_hash="h" * 64, text=text, posted_at=NOW, fetched_at=NOW)
    ev = contrast_check(FakeGemini([item()]), c, CLAIM, [passage()])
    seen = []
    graph_run = AgentRun()
    graph = build_claim_agent(c, ledger, post, lambda p: ExtractionResult("ok", [claim()], model="m", prompt_sha256="p"),
                              lambda t, lang: FactCheckResult([], 0, False), None, None, graph_run, lambda text: seen.append(text) or ev)

    async def go():
        return [e async for e in trace_events(graph, {"job": "j", "text": CLAIM}, "j", c.runtime.trace)]

    events = asyncio.run(go())
    tools = [e.data["name"] for e in events if e.kind == "tool_call"]
    assert "official text (RBI, SEBI)" in tools and seen == [CLAIM]
    res = graph_run.outcome.results[0]
    assert res.outcome.verdict.label == "REFUTED" and ledger.verify().ok
    assert any(x["summary"].startswith("1 passage") for x in [e.data for e in events if e.kind == "tool_call" and e.data["name"].startswith("official")])
