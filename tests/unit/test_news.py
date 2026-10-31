"""News-outlet check: feed parsing with the host gate, word coverage with stems, the two honest readings, and the route."""

from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient

from satyasutra.api.app import create_app
from satyasutra.config import load_config
from satyasutra.sources.index import OfficialIndex
from satyasutra.sources.news import check_news, content_words, coverage, parse_news, refresh_news, weights

CFG = load_config()
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
FEED = CFG.runtime.news.feeds[0]                                  # The Hindu; item links must be on thehindu.com

RSS = f"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Heavy rain alert for Kerala and Karnataka</title><link>https://www.thehindu.com/news/a1</link><description>IMD warns of heavy rainfall</description>
<pubDate>Sat, 31 Oct 2026 10:00:00 +0000</pubDate></item>
<item><title>Parliament session begins</title><link>https://evil.example.com/steal</link><description>x</description></item>
<item><title>Prime Minister inaugurates new bridge</title><link>https://www.thehindu.com/news/a2</link><description></description></item>
</channel></rss>""".encode()


def loaded(tmp_path) -> OfficialIndex:
    index = OfficialIndex(tmp_path / "news.sqlite3")
    for p in parse_news(RSS, FEED, CFG, NOW):
        index.add(p)
    return index


def test_items_off_the_outlets_own_hosts_are_skipped():
    items = parse_news(RSS, FEED, CFG, NOW)
    assert [p.url for p in items] == ["https://www.thehindu.com/news/a1", "https://www.thehindu.com/news/a2"]
    assert all(p.tier == 3 for p in items)                          # news is never Tier 1 or 2


def test_words_match_by_stem_and_stopwords_are_ignored():
    words = content_words("The minister was assassinated", CFG)
    assert "the" not in words and "was" not in words and "assassinated" in words
    share, matched = coverage(["assassinated"], "Minister assassination attempt foiled", weights(["assassinated"], [], CFG), CFG)
    assert matched == 1 and share == 1.0                             # assassinated ~ assassination share six leading letters


def test_a_matching_headline_is_reported_with_its_link_and_a_caution(tmp_path):
    r = check_news(CFG, loaded(tmp_path), "Heavy rain alert in Kerala")
    assert r.status == "reported" and r.hits[0].url.endswith("/a1") and "not proof the claim is true" in r.note and r.outlets_with_hits == 1


def test_no_match_says_how_far_back_we_looked_and_that_it_is_not_proof(tmp_path):
    r = check_news(CFG, loaded(tmp_path), "Prime Minister was assassinated")
    assert r.status == "not_found" and r.hits == [] and "not proof" in r.note and r.items_checked == 2 and "2026-10-31" in r.note


def test_an_empty_index_or_a_claim_without_words_is_not_read_as_absence(tmp_path):
    assert check_news(CFG, OfficialIndex(tmp_path / "e.sqlite3"), "something happened").status == "unavailable"
    assert check_news(CFG, loaded(tmp_path), "it is the").status == "no_query"


def test_a_failing_feed_is_reported_by_class_and_the_rest_still_load(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.endswith("thehindu.com"):
            return httpx.Response(200, content=RSS)
        return httpx.Response(503) if "ndtv" in request.url.host or "feedburner" in request.url.host else httpx.Response(200, content=b"not xml")
    status = refresh_news(CFG, OfficialIndex(tmp_path / "n.sqlite3"), http=httpx.Client(transport=httpx.MockTransport(handler)), now=NOW)
    assert status["news_the_hindu"].startswith("ok: 2") and status["news_ndtv"] == "HTTP 503" and status["news_toi"] == "ParseError"


def test_the_route_returns_hits_and_never_a_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr("satyasutra.api.app.refresh_news_if_stale", lambda cfg, index: None)
    index = loaded(tmp_path)
    cfg = CFG.model_copy(update={"runtime": CFG.runtime.model_copy(update={"news": CFG.runtime.news.model_copy(update={"db_path": str(index.path)})})})
    body = TestClient(create_app(cfg)).get("/news/check", params={"text": "Heavy rain alert in Kerala"}).json()
    assert body["status"] == "reported" and body["hits"][0]["publisher"] == "The Hindu" and "label" not in body


def test_a_headline_sharing_only_common_words_does_not_cover_a_claim_whose_rare_word_is_missing(tmp_path):
    index = loaded(tmp_path)
    w = weights(["prime", "minister", "assassinated"], index.texts(), CFG)
    assert w["assassinated"] > w["minister"]                          # nobody has printed it: it weighs most
    assert check_news(CFG, index, "Prime Minister was assassinated").status == "not_found"
    assert check_news(CFG, index, "Prime Minister inaugurates a bridge").status == "reported"


def test_the_claims_most_distinctive_word_must_be_in_the_headline_even_when_the_names_match(tmp_path):
    index = OfficialIndex(tmp_path / "n.sqlite3")
    for i, title in enumerate(["PM Modi inaugurates tourism projects in Gujarat", "Prime Minister Modi addresses rally", "Minister Modi visits Delhi",
                               "Heavy rain alert for Kerala", "Stocks close higher", "Cricket final today", "New metro line opens", "Budget session begins"]):
        index.add(next(iter(parse_news(f"<rss><channel><item><title>{title}</title><link>https://www.thehindu.com/n/{i}</link></item></channel></rss>".encode(),
                                       FEED, CFG, NOW))))
    assert check_news(CFG, index, "Prime Minister Modi was assassinated").status == "not_found"
    assert check_news(CFG, index, "Prime Minister Modi addresses rally").status == "reported"
