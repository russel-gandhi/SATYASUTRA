"""Is a claim in the news? A live look at the headlines of major outlets, as context for claims no fact-checker has covered.

This never decides a verdict and is never Tier 1 or 2 evidence. Two readings only: matching headlines exist (shown with links; being
reported is not being true, since outlets also report rumours to debunk them), or none matches in the recent items we hold (a signal
for something that would be major news, not proof, since feeds hold only recent items and stories are worded differently).
Only headlines and links are shown. A feed item whose link is off its configured hosts is skipped; a failing feed is reported by
class and never raises.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from ..config import Config, NewsFeed
from ..schemas.core import sha256_text
from .index import OfficialIndex, Passage
from .official import clean, host_allowed, parse_time
from .probe import build_user_agent

WORD = re.compile(r"\w+", re.UNICODE)
TIER = 3                                  # news reporting: context only
EPOCH = datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class NewsHit:
    publisher: str
    title: str
    url: str
    published_at: datetime | None
    coverage: float                       # share of the claim's content words found in the headline and summary


@dataclass
class NewsResult:
    status: str                           # reported, not_found, unavailable or no_query
    hits: list[NewsHit] = field(default_factory=list)
    outlets_checked: int = 0
    outlets_with_hits: int = 0
    items_checked: int = 0
    oldest: str = ""
    newest: str = ""
    note: str = ""


def parse_news(body: bytes, feed: NewsFeed, cfg: Config, fetched_at: datetime) -> list[Passage]:
    s = cfg.runtime.news
    root = ET.fromstring(body.lstrip(b"\xef\xbb\xbf"))
    out: list[Passage] = []
    for item in root.iter("item"):
        link = (item.findtext("link") or item.findtext("guid") or "").strip()
        title = clean(item.findtext("title") or "")
        if not link or not title or not host_allowed(link, feed.hosts):
            continue
        summary = clean(item.findtext("description") or "")
        text = f"{title}. {summary if summary != title else ''}".strip()[:s.max_passage_chars]
        out.append(Passage(feed.id, feed.publisher, TIER, link, title, text, sha256_text(f"{feed.id}|{link}"), parse_time(item.findtext("pubDate")), fetched_at))
        if len(out) >= s.max_items_per_feed:
            break
    return out


def refresh_news(cfg: Config, index: OfficialIndex, http: httpx.Client | None = None, now: datetime | None = None) -> dict[str, str]:
    s = cfg.runtime.news
    now = now or datetime.now(timezone.utc)
    client = http or httpx.Client(timeout=s.timeout_s, headers={"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}, follow_redirects=True)
    status: dict[str, str] = {}
    try:
        for feed in s.feeds:
            try:
                r = client.get(feed.url)
                if r.status_code != httpx.codes.OK:
                    status[feed.id] = f"HTTP {r.status_code}"
                    continue
                items = parse_news(r.content, feed, cfg, now)
                status[feed.id] = f"ok: {sum(index.add(p) for p in items)} new of {len(items)}"
            except (httpx.HTTPError, ET.ParseError) as exc:          # class only: the message can carry the URL
                status[feed.id] = type(exc).__name__
    finally:
        if http is None:
            client.close()
    index.mark_refreshed(now)
    return status


def refresh_news_if_stale(cfg: Config, index: OfficialIndex, http: httpx.Client | None = None, now: datetime | None = None) -> dict[str, str] | None:
    now = now or datetime.now(timezone.utc)
    last = index.refreshed_at()
    if last is not None and (now - last).total_seconds() < cfg.runtime.news.refresh_s:
        return None
    return refresh_news(cfg, index, http, now)


def content_words(text: str, cfg: Config) -> list[str]:
    s = cfg.runtime.news
    stop = {w.casefold() for w in s.stopwords}
    return list(dict.fromkeys(w for w in WORD.findall(text.casefold()) if len(w) >= s.min_token_chars and w not in stop))


def _key(word: str, stem: int) -> str:
    """Words that share their leading characters count as the same word (assassinated ~ assassination)."""
    return word[:stem]


def _keys(text: str, stem: int) -> set[str]:
    return {_key(w, stem) for w in WORD.findall(text.casefold())}


def weights(words: list[str], corpus: list[str], cfg: Config) -> dict[str, float]:
    """Rarer words weigh more, from how many held headlines contain them (inverse document frequency). A word no headline contains weighs most:
    a claim whose rare word is missing is not covered by a headline that shares only its common words (a Prime Minister inaugurating a
    bridge does not report one being assassinated)."""
    stem = cfg.runtime.news.stem_chars
    docs = [_keys(t, stem) for t in corpus]
    return {w: math.log((len(docs) + 1) / (sum(_key(w, stem) in d for d in docs) + 1)) + 1.0 for w in words}


def coverage(words: list[str], passage_text: str, weight: dict[str, float], cfg: Config) -> tuple[float, int]:
    """(weighted share of the claim's words found in the passage, how many words were found)."""
    have = _keys(passage_text, cfg.runtime.news.stem_chars)
    found = [w for w in words if _key(w, cfg.runtime.news.stem_chars) in have]
    total = sum(weight[w] for w in words)
    return (sum(weight[w] for w in found) / total if total else 0.0), len(found)


def check_news(cfg: Config, index: OfficialIndex, text: str) -> NewsResult:
    s = cfg.runtime.news
    words = content_words(text, cfg)
    if not words:
        return NewsResult("no_query", note="The claim has no content words to look for.")
    items, oldest, newest = index.span()
    if not items:
        return NewsResult("unavailable", note="No news headlines are loaded, so nothing could be said about whether this is reported.")
    candidates = index.search(" ".join(words), s.max_candidates, s.min_token_chars)
    weight = weights(words, index.texts(), cfg)
    rarest = _key(max(words, key=weight.__getitem__), s.stem_chars)      # the claim's most distinctive word (the event: 'assassinated') must be there
    hits: list[NewsHit] = []
    for p in candidates:
        share, matched = coverage(words, p.text, weight, cfg)
        if share >= s.min_coverage and matched >= s.min_matched_words and rarest in _keys(p.text, s.stem_chars):
            hits.append(NewsHit(p.publisher, p.title, p.url, p.published_at, round(share, 2)))
    hits.sort(key=lambda h: (h.coverage, h.published_at or EPOCH), reverse=True)
    result = NewsResult("reported" if hits else "not_found", hits[:s.max_results], len(s.feeds), len({h.publisher for h in hits}), items, oldest, newest)
    result.note = ("Matching headlines are shown. Being reported is not proof the claim is true: outlets also report rumours in order to debunk them."
                   if hits else
                   f"No headline among the {items} most recent items from {len(s.feeds)} outlets (published {oldest[:16]} to {newest[:16]} UTC) matches. "
                   "For a claim that would be major news this is a signal, not proof: feeds hold only recent items and a story may be worded differently.")
    return result
