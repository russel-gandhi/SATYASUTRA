"""India pulse: what Indian media, searchers and fact-checkers are busy with right now, fetched live.

Three lanes (config `runtime.pulse`):
  trending  Google Trends India search terms, with the traffic band and the outlets covering each term,
  rumours   the newest articles of Indian fact-checkers (BOOM, Alt News, Factly, Vishvas News, Newschecker; English, Hindi, Kannada, Telugu): what people are asking about now,
  stories   top headlines of Indian outlets in English, Hindi and Kannada, grouped so one story carried by several outlets is one row with its outlet count.
It is a window on Indian media and search attention, not a measure of what spreads on WhatsApp: no public source shows that, and the page says so. Nothing here decides
a verdict; a row only offers to be checked. A feed that fails is named by class and the other lanes still answer. The snapshot is cached for `refresh_s`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock

import httpx

from .config import Config, PulseFeed
from .fastclock.normalise import normalise
from .sources.news import content_words, coverage, weights
from .sources.official import clean, parse_time
from .sources.probe import build_user_agent
from .sources.websearch import outlet_suffix

def parse_items(body: bytes, feed: PulseFeed, now: datetime, cfg: Config) -> list[dict]:
    """Plain RSS items to rows: headline, outlet, link, time. Google News carries the real outlet in <source>; other feeds are the publisher's own."""
    root = ET.fromstring(body.lstrip(b"\xef\xbb\xbf"))
    out: list[dict] = []
    for item in root.iter("item"):
        link, title = (item.findtext("link") or "").strip(), clean(item.findtext("title") or "")
        outlet = clean(item.findtext("source") or "") or feed.publisher
        if not link or not title:
            continue
        out.append({"text": outlet_suffix(title, outlet), "outlet": outlet, "url": link, "published_at": parse_time(item.findtext("pubDate")), "language": feed.language, "feed": feed.id})
    return out


def parse_trends(body: bytes, cfg: Config) -> list[dict]:
    s = cfg.runtime.pulse
    ns = s.trends_namespace
    root = ET.fromstring(body.lstrip(b"\xef\xbb\xbf"))
    out = []
    for item in root.iter("item"):
        term = clean(item.findtext("title") or "")
        if not term:
            continue
        news = [{"title": clean(n.findtext(ns + "news_item_title") or ""), "outlet": clean(n.findtext(ns + "news_item_source") or ""), "url": (n.findtext(ns + "news_item_url") or "").strip()}
                for n in item.findall(ns + "news_item")][: s.trend_news_items]
        out.append({"term": term, "traffic": clean(item.findtext(ns + "approx_traffic") or ""), "published_at": parse_time(item.findtext("pubDate")), "news": [n for n in news if n["url"]]})
        if len(out) >= s.max_trends:
            break
    return out


EPOCH = datetime.min.replace(tzinfo=timezone.utc)
ARTICLE = "the "


def _outlet_key(name: str) -> str:
    """'The Times of India' and 'Times of India' are one outlet."""
    low = " ".join(name.casefold().split())
    return low[len(ARTICLE):] if low.startswith(ARTICLE) else low


def cluster_stories(items: list[dict], cfg: Config) -> list[dict]:
    """Group headlines about one story. Words that appear in many headlines are measured from this very batch and ignored for linking (no fixed stop-word list, so it
    works for Hindi and Kannada too); two headlines join when their remaining words overlap above `cluster_jaccard`. Per language: headlines are not translated."""
    s = cfg.runtime.pulse
    out: list[dict] = []
    for lang in sorted({i["language"] for i in items}):
        mine = sorted((i for i in items if i["language"] == lang), key=lambda i: i["published_at"] or EPOCH)
        sets = [{w for w in normalise(i["text"]).split() if len(w) >= s.min_token_chars} for i in mine]
        df = Counter(w for ws in sets for w in ws)
        common = {w for w, n in df.items() if n > s.common_word_share * len(mine)}
        groups: list[dict] = []
        for it, ws in zip(mine, sets):
            key = ws - common
            home = next((g for g in groups if key and len(key & g["words"]) / len(key | g["words"]) >= s.cluster_jaccard), None)
            if home is None:
                home = {"words": set(key), "items": []}
                groups.append(home)
            home["words"] |= key
            home["items"].append(it)
        found = []
        for g in groups:
            outlets = list({_outlet_key(i["outlet"]): i["outlet"] for i in g["items"]}.values())
            if len(outlets) < s.min_outlets:
                continue
            times = [i["published_at"] for i in g["items"] if i["published_at"]]
            found.append({"text": g["items"][-1]["text"], "language": lang, "outlets": outlets, "size": len(outlets), "first_seen": min(times).isoformat() if times else None,
                          "latest": max(times).isoformat() if times else None,
                          "items": [{"outlet": i["outlet"], "title": i["text"], "url": i["url"], "published_at": i["published_at"].isoformat() if i["published_at"] else None} for i in g["items"][:8]]})
        found.sort(key=lambda g: (g["size"], g["latest"] or ""), reverse=True)
        out.extend(found[: s.max_stories])
    return out


class Pulse:
    def __init__(self, cfg: Config, http: httpx.Client | None = None):
        self.cfg, self._http, self._lock, self._snap, self._at = cfg, http, Lock(), None, None

    def _get(self, client: httpx.Client, url: str) -> bytes:
        r = client.get(url)
        r.raise_for_status()
        return r.content

    def _fetch(self, client: httpx.Client, feed: PulseFeed, now: datetime) -> tuple[str, list[dict], str]:
        try:
            return feed.id, parse_items(self._get(client, feed.url), feed, now, self.cfg), "ok"
        except (httpx.HTTPError, ET.ParseError) as exc:        # the class only: the text can carry the URL
            return feed.id, [], type(exc).__name__

    def refresh(self, now: datetime | None = None) -> dict:
        s = self.cfg.runtime.pulse
        now = now or datetime.now(timezone.utc)
        client = self._http or httpx.Client(timeout=s.timeout_s, headers={"User-Agent": build_user_agent(self.cfg.runtime.probe.user_agent)}, follow_redirects=True)
        status: dict[str, str] = {}
        try:
            with ThreadPoolExecutor(max_workers=s.workers) as pool:
                trends_future = pool.submit(lambda: parse_trends(self._get(client, s.trends_url), self.cfg))
                heads = list(pool.map(lambda f: self._fetch(client, f, now), s.headline_feeds))
                facts = list(pool.map(lambda f: self._fetch(client, f, now), s.fact_check_feeds))
                try:
                    trends = trends_future.result()
                    status["trends"] = f"ok: {len(trends)}"
                except (httpx.HTTPError, ET.ParseError) as exc:
                    trends, status["trends"] = [], type(exc).__name__
        finally:
            if self._http is None:
                client.close()
        for fid, rows, st in [*heads, *facts]:
            status[fid] = f"ok: {len(rows)}" if st == "ok" else st
        cutoff = now - timedelta(hours=s.max_age_hours)
        fresh = lambda r: r["published_at"] is None or r["published_at"] >= cutoff
        headlines = [r for _, rows, _ in heads for r in rows if fresh(r)]
        rumours = sorted((r for _, rows, _ in facts for r in rows if fresh(r) and r["published_at"]), key=lambda r: r["published_at"], reverse=True)[: s.max_rumours]
        stamp = lambda r: {**r, "published_at": r["published_at"].isoformat() if r["published_at"] else None}
        return {"fetched_at": now.isoformat(), "sources": status, "trending": [{**t, "published_at": t["published_at"].isoformat() if t["published_at"] else None} for t in trends],
                "rumours": [stamp(r) for r in rumours], "stories": cluster_stories(headlines, self.cfg), "headlines_read": len(headlines),
                "_corpus": [r["text"] for r in [*headlines, *rumours]],
                "note": "A window on Indian media and search attention right now, fetched live. It does not show what spreads on WhatsApp, which no public source shows."}

    def snapshot(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        with self._lock:                                         # one refresh at a time; callers during a refresh wait for it and share the result
            if self._snap is None or self._at is None or (now - self._at).total_seconds() >= self.cfg.runtime.pulse.refresh_s:
                self._snap, self._at = self.refresh(now), now
            return self._snap

    def match(self, text: str) -> dict:
        """Is this claim already in the Indian news or fact-checkers' latest articles? Uses the same rarity-weighted word coverage as the news check."""
        snap = self.snapshot()
        words = content_words(text, self.cfg)
        if not words:
            return {"stories": [], "fact_checks": []}
        w = weights(words, snap["_corpus"], self.cfg)
        floor = self.cfg.runtime.news.min_coverage
        def hits(rows, get):
            scored = [(coverage(words, get(r), w, self.cfg)[0], r) for r in rows]
            return [r for share, r in sorted(scored, key=lambda p: -p[0]) if share >= floor]
        return {"stories": hits(snap["stories"], lambda g: " ".join(i["title"] for i in g["items"]))[:3], "fact_checks": hits(snap["rumours"], lambda r: r["text"])[:3]}
