"""Official text: RSS feeds of Tier 1 publishers, fetched live into a searchable index.

The gate comes first: a feed or an item link whose host is not in `policy.allowed_domains` is never fetched or stored. The client
identifies itself honestly (the descriptive User-Agent); publishers that refuse that (PIB answers 403 to it, see config/sources.yaml)
are simply not used, we do not disguise the client. A failing feed is reported by name and class and never raises, so one outage
cannot take the others down.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx

from ..config import Config, OfficialFeed
from ..schemas import Evidence
from ..schemas.core import sha256_text
from .index import OfficialIndex, Passage
from .probe import build_user_agent

TAG = re.compile(r"<[^>]+>")
DATE_FORMATS = ("%d %b, %Y %z", "%d %b %Y %z")


def host_allowed(url: str, allowed: list[str]) -> bool:
    host = (urlparse(url).hostname or "").casefold()
    return any(host == d.casefold() or host.endswith("." + d.casefold()) for d in allowed)


def clean(text: str) -> str:
    return " ".join(html.unescape(TAG.sub(" ", text or "")).split())


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        parsed = None
        for fmt in DATE_FORMATS:
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def parse_feed(body: bytes, feed: OfficialFeed, cfg: Config, fetched_at: datetime) -> list[Passage]:
    """RSS 2.0 items to passages. Items whose link is outside the allowed domains, or that have no link or text, are skipped."""
    s, allowed = cfg.runtime.official, cfg.policy.allowed_domains
    root = ET.fromstring(body.lstrip(b"\xef\xbb\xbf"))
    out: list[Passage] = []
    for item in root.iter("item"):
        link = (item.findtext("link") or item.findtext("guid") or "").strip()
        title = clean(item.findtext("title") or "")
        text = clean(item.findtext("description") or "")
        if not link or not title or not host_allowed(link, allowed):
            continue
        passage = f"{feed.publisher}: {title}. {text if text and text != title else ''}".strip()[:s.max_passage_chars]
        out.append(Passage(feed.id, feed.publisher, feed.tier, link, title, passage, sha256_text(passage), parse_time(item.findtext("pubDate")), fetched_at))
        if len(out) >= s.max_items_per_feed:
            break
    return out


def refresh(cfg: Config, index: OfficialIndex, http: httpx.Client | None = None, now: datetime | None = None) -> dict[str, str]:
    """Fetch every configured feed into the index. Returns feed id -> 'ok: N new' or the failure class."""
    s, allowed = cfg.runtime.official, cfg.policy.allowed_domains
    now = now or datetime.now(timezone.utc)
    client = http or httpx.Client(timeout=s.timeout_s, headers={"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}, follow_redirects=True)
    status: dict[str, str] = {}
    try:
        for feed in s.feeds:
            if not host_allowed(feed.url, allowed):
                status[feed.id] = "blocked by the allowed-domains gate"
                continue
            try:
                r = client.get(feed.url)
                if r.status_code != httpx.codes.OK:
                    status[feed.id] = f"HTTP {r.status_code}"
                    continue
                passages = parse_feed(r.content, feed, cfg, now)
                status[feed.id] = f"ok: {sum(index.add(p) for p in passages)} new of {len(passages)}"
            except (httpx.HTTPError, ET.ParseError) as exc:      # the class only: the text can carry the URL
                status[feed.id] = type(exc).__name__
    finally:
        if http is None:
            client.close()
    index.mark_refreshed(now)
    return status


def refresh_if_stale(cfg: Config, index: OfficialIndex, http: httpx.Client | None = None, now: datetime | None = None) -> dict[str, str] | None:
    now = now or datetime.now(timezone.utc)
    last = index.refreshed_at()
    if last is not None and (now - last).total_seconds() < cfg.runtime.official.refresh_s:
        return None
    return refresh(cfg, index, http, now)


def passage_evidence(p: Passage) -> Evidence:
    """A retrieved official passage as context evidence. It decides nothing until the contrast step ties it to a claim."""
    return Evidence(role="context", route="verifier", source_id=p.source_id, url=p.url, fetched_at=p.fetched_at, tier=p.tier,  # type: ignore[arg-type]
                    passage=p.text, passage_sha256=p.sha256, publisher=p.publisher, reviewed_at=p.published_at)
