"""Searching the live web's news for a claim: a news-search RSS feed returns headlines with the outlet and the publication date.

This is how the system goes and looks instead of waiting for a fact-checker to have covered something. A hit is only a headline, an outlet name,
a date and a link; what it says about the claim is judged in claims/web.py and decided by the rules. A failing search is reported by class and never raises
into the caller's verdict.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

import httpx

from ..config import Config
from .official import clean, parse_time
from .probe import build_user_agent


@dataclass
class WebHit:
    outlet: str
    title: str
    url: str
    published_at: datetime | None


def outlet_suffix(title: str, outlet: str) -> str:
    """News search appends ' - Outlet' to every title; the outlet is shown separately, so drop it."""
    tail = f" - {outlet}"
    return title[: -len(tail)].rstrip() if outlet and title.endswith(tail) else title


def parse_hits(body: bytes, cfg: Config) -> list[WebHit]:
    s = cfg.runtime.web
    root = ET.fromstring(body.lstrip(b"\xef\xbb\xbf"))
    hits: list[WebHit] = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        outlet = clean(item.findtext("source") or "")
        title = clean(item.findtext("title") or "")
        if not link or not title or not outlet:
            continue
        hits.append(WebHit(outlet, outlet_suffix(title, outlet)[: s.title_chars], link, parse_time(item.findtext("pubDate"))))
    return hits


def search_web(cfg: Config, query: str, http: httpx.Client | None = None) -> list[WebHit]:
    s = cfg.runtime.web
    q = " ".join(query.split())[: s.max_query_chars]
    if not q:
        return []
    client = http or httpx.Client(timeout=s.timeout_s, headers={"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}, follow_redirects=True)
    try:
        r = client.get(s.endpoint, params={"q": q, "hl": s.language, "gl": s.region, "ceid": s.edition})
        r.raise_for_status()
        return parse_hits(r.content, cfg)
    finally:
        if http is None:
            client.close()
