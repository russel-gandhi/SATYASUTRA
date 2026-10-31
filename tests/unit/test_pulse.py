from datetime import datetime, timezone

import httpx

from satyasutra.config import PulseFeed, load_config
from satyasutra.officials import annotate
from satyasutra.pulse import Pulse, cluster_stories, parse_items, parse_trends

CFG = load_config()
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
FEED = PulseFeed(id="gn-en", language="en", publisher="Google News", url="https://news.example/rss")

# The shape Google News RSS and Google Trends RSS answer with (captured from the live feeds): outlet in <source>, titles end with " - Outlet".
NEWS = b"""\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>Bridge collapses in Gujarat after heavy rain - The Hindu</title><link>https://a.example/1</link><pubDate>Sat, 31 Oct 2026 10:00:00 GMT</pubDate><source url="https://thehindu.com">The Hindu</source></item>
<item><title>Bridge collapses in Gujarat after heavy rain - Times of India</title><link>https://a.example/2</link><pubDate>Sat, 31 Oct 2026 10:30:00 GMT</pubDate><source url="https://toi.com">Times of India</source></item>
<item><title>Bridge collapses in Gujarat after heavy rain - The Times of India</title><link>https://a.example/3</link><pubDate>Sat, 31 Oct 2026 10:40:00 GMT</pubDate><source url="https://toi.com">The Times of India</source></item>
<item><title>Rupee ends flat against dollar - Mint</title><link>https://a.example/4</link><pubDate>Sat, 31 Oct 2026 09:00:00 GMT</pubDate><source url="https://mint.com">Mint</source></item>
<item><title>No link here</title><pubDate>Sat, 31 Oct 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""

FILLER = "".join(f"<item><title>{w} announces new scheme today - Outlet{i}</title><link>https://f.example/{i}</link><pubDate>Sat, 31 Oct 2026 08:00:00 GMT</pubDate><source url=\"https://o.com\">Outlet{i}</source></item>"
                 for i, w in enumerate(f"{x}{y}ina" for x in "abcdefghij" for y in "klmnop")).encode()
NEWS = NEWS.replace(b"</channel>", FILLER + b"</channel>")      # a real batch has many unrelated headlines; word rarity is measured against it

TRENDS = b"""<?xml version="1.0"?><rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>gujarat bridge</title><ht:approx_traffic>200+</ht:approx_traffic><pubDate>Sat, 31 Oct 2026 11:00:00 +0000</pubDate>
<ht:news_item><ht:news_item_title>Bridge falls</ht:news_item_title><ht:news_item_url>https://a.example/1</ht:news_item_url><ht:news_item_source>The Hindu</ht:news_item_source></ht:news_item>
<ht:news_item><ht:news_item_title>No url</ht:news_item_title><ht:news_item_url></ht:news_item_url><ht:news_item_source>X</ht:news_item_source></ht:news_item></item>
</channel></rss>"""


def test_parse_items_reads_outlet_from_source_and_skips_rows_without_link():
    rows = parse_items(NEWS, FEED, NOW, CFG)
    assert len(rows) == 4 + 60
    assert rows[0]["outlet"] == "The Hindu" and rows[0]["text"] == "Bridge collapses in Gujarat after heavy rain"
    assert rows[0]["language"] == "en" and rows[0]["published_at"] is not None


def test_parse_trends_keeps_term_traffic_and_only_news_with_links():
    t = parse_trends(TRENDS, CFG)
    assert t[0]["term"] == "gujarat bridge" and t[0]["traffic"] == "200+"
    assert [n["outlet"] for n in t[0]["news"]] == ["The Hindu"]


def test_one_outlet_under_two_names_is_counted_once():
    stories = cluster_stories(parse_items(NEWS, FEED, NOW, CFG), CFG)
    bridge = [s for s in stories if "Bridge" in s["text"]]
    assert len(bridge) == 1 and bridge[0]["size"] == 2        # The Hindu + Times of India (not three)


def test_languages_are_never_mixed_in_a_cluster():
    filler = [{"text": f"शब्द{i} नई योजना", "outlet": f"F{i}", "url": f"https://h.example/f{i}", "published_at": NOW, "language": "hi", "feed": "x"} for i in range(60)]
    hi = [{"text": "पुल गिरा गुजरात", "outlet": o, "url": f"https://h.example/{o}", "published_at": NOW, "language": "hi", "feed": "x"} for o in ("A", "B")]
    en = [{"text": "पुल गिरा गुजरात", "outlet": "C", "url": "https://h.example/C", "published_at": NOW, "language": "en", "feed": "x"}]
    stories = [s for s in cluster_stories(filler + hi + en, CFG) if s["size"] > 1]
    assert [s["language"] for s in stories] == ["hi"] and stories[0]["size"] == 2


def _client(body):
    def handler(request):
        if "trends" in request.url.path or request.url.host == "trends.example":
            return httpx.Response(200, content=TRENDS)
        return httpx.Response(200, content=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_failing_feed_is_named_by_class_and_the_others_still_answer():
    cfg = CFG.model_copy(deep=True)
    p = cfg.runtime.pulse
    p.headline_feeds = [FEED, FEED.model_copy(update={"id": "bad", "url": "https://bad.example/rss"})]
    p.fact_check_feeds = []
    p.trends_url = "https://trends.example/rss"

    def handler(request):
        if request.url.host == "bad.example":
            return httpx.Response(403)
        return httpx.Response(200, content=TRENDS if request.url.host == "trends.example" else NEWS)

    snap = Pulse(cfg, httpx.Client(transport=httpx.MockTransport(handler))).refresh(NOW)
    assert snap["sources"]["gn-en"].startswith("ok") and snap["sources"]["bad"] == "HTTPStatusError"
    assert snap["trending"] and snap["stories"]
    assert "http" not in " ".join(snap["sources"].values())      # no URL in a status


def test_match_finds_a_claim_in_current_coverage_and_not_an_unrelated_one():
    cfg = CFG.model_copy(deep=True)
    cfg.runtime.pulse.headline_feeds, cfg.runtime.pulse.fact_check_feeds = [FEED], []
    cfg.runtime.pulse.trends_url = "https://trends.example/rss"
    pulse = Pulse(cfg, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=TRENDS if r.url.host == "trends.example" else NEWS))))
    pulse._snap, pulse._at = pulse.refresh(NOW), datetime.now(timezone.utc)
    assert pulse.match("Bridge collapses in Gujarat after heavy rain")["stories"]
    assert not pulse.match("Cricket team wins the world cup final")["stories"]


def test_annotate_puts_state_from_cache_and_hides_the_private_corpus():
    snap = {"fetched_at": NOW.isoformat(), "sources": {}, "note": "n", "headlines_read": 1, "trending": [], "rumours": [{"text": "Flood warning for the coast", "language": "en"}], "stories": [], "_corpus": ["x"]}
    out = annotate(snap, lambda text: None, CFG)
    assert "_corpus" not in out and out["rumours"][0]["state"]["checked"] is False
