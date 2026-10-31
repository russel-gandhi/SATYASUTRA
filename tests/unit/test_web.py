"""Web news search: the parser, the outlet tiers, and the counts that turn labelled headlines into support or a debunk."""

from datetime import datetime, timezone
from types import SimpleNamespace

from satyasutra.claims import check_web
from satyasutra.claims.rules import decide
from satyasutra.config import load_config
from satyasutra.schemas import Claim
from satyasutra.sources.websearch import WebHit, outlet_suffix, parse_hits

NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)
CFG = load_config()
RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Actor dies at 75 - NDTV</title><link>https://news.example/a</link><pubDate>Fri, 09 Oct 2026 10:00:00 GMT</pubDate><source url="https://ndtv.com">NDTV</source></item>
<item><title>No source here</title><link>https://news.example/b</link></item></channel></rss>"""


def hits(*outlets):
    return [WebHit(o, f"headline {i}", f"https://news.example/{i}", NOW) for i, o in enumerate(outlets)]


def run(outlets, stances):
    client = SimpleNamespace(model="m", generate_json=lambda prompt, schema: SimpleNamespace(data={"items": [{"headline": i + 1, "stance": s} for i, s in enumerate(stances)]}))
    return check_web(client, CFG, "the claim", now=NOW, search=lambda cfg, q, http: hits(*outlets))


def test_the_parser_keeps_outlet_title_date_and_link_and_drops_items_without_an_outlet():
    out = parse_hits(RSS, CFG)
    assert len(out) == 1 and out[0].outlet == "NDTV" and out[0].title == "Actor dies at 75" and out[0].published_at.year == 2026
    assert outlet_suffix("Story - BBC", "BBC") == "Story" and outlet_suffix("Story", "BBC") == "Story"


def test_two_reputable_outlets_reporting_it_as_fact_support_it_and_an_unknown_blog_does_not_count():
    r = run(["NDTV", "The Hindu", "Some Blog"], ["reports_as_fact"] * 3)
    assert sorted(r.supporting) == ["NDTV", "The Hindu"] and {e.role for e in r.evidence if e.publisher == "Some Blog"} == {"context"}
    assert [e.tier for e in r.evidence] == [2, 2, 3]


def test_one_reputable_outlet_alone_is_only_context():
    r = run(["NDTV", "Some Blog"], ["reports_as_fact"] * 2)
    assert r.supporting == [] and {e.role for e in r.evidence} == {"context"}


def test_one_fact_checker_debunking_is_enough_but_one_ordinary_outlet_is_not():
    assert run(["Alt News"], ["debunks"]).debunking == ["Alt News"]
    assert run(["NDTV"], ["debunks"]).debunking == []
    assert sorted(run(["NDTV", "The Hindu"], ["debunks", "debunks"]).debunking) == ["NDTV", "The Hindu"]


def test_a_second_copy_from_the_same_outlet_adds_nothing():
    r = run(["NDTV", "NDTV"], ["reports_as_fact", "reports_as_fact"])
    assert r.supporting == [] and len([e for e in r.evidence if e.role != "context"]) == 0


def test_the_rules_turn_web_support_into_supported_at_medium_at_most_and_a_debunk_into_refuted():
    claim = Claim(id="c1", text_orig="x", text_en="the claim", lang="en", post_id="p", span=(0, 1))
    r = run(["NDTV", "The Hindu", "BBC"], ["reports_as_fact"] * 3)
    out = decide(claim, r.evidence, CFG)
    assert out.verdict.label == "SUPPORTED" and out.verdict.confidence_band == "medium"
    d = run(["Alt News"], ["debunks"])
    assert decide(claim, d.evidence, CFG).verdict.label == "REFUTED"


def test_support_and_a_debunk_together_decide_nothing():
    claim = Claim(id="c1", text_orig="x", text_en="the claim", lang="en", post_id="p", span=(0, 1))
    r = run(["NDTV", "The Hindu", "Alt News"], ["reports_as_fact", "reports_as_fact", "debunks"])
    out = decide(claim, r.evidence, CFG)
    assert out.verdict.label == "UNVERIFIABLE_YET" and "CONFLICT" in out.verdict.flags


def test_a_failed_search_or_a_busy_model_is_reported_not_raised():
    def boom(cfg, q, http):
        raise ValueError("bad feed")
    client = SimpleNamespace(model="m", generate_json=lambda p, s: None)
    assert check_web(client, CFG, "x", now=NOW, search=boom).error == "ValueError"
