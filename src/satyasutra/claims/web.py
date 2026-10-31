"""Go and look: what do news outlets across the web say about this claim?

The search (sources/websearch.py) returns headlines with outlet, date and link. Gemini only labels each headline against the claim
(reports it as fact, debunks it, only mentions the topic, or is about something else); it cannot add a headline or an outlet. Everything
that decides is plain code:
  - an outlet is trusted only if it is on the configured lists (reputable news outlets and fact-checkers, Tier 2); anything else is Tier 3 and cannot decide,
  - support needs `min_outlets_support` distinct trusted outlets reporting it as fact,
  - a debunk counts from one fact-checker, or from `min_outlets_debunk` distinct trusted outlets,
  - what does not reach those counts is kept as context, never as support or contradiction.
The lists and counts are policy choices, not calibrated. Outlets repeat each other and wire copy, so "N outlets" is not N independent
confirmations; the rules cap news-only support at medium confidence for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import re

import httpx

from ..config import Config
from ..errors import SourceError
from ..llm import GeminiClient
from ..schemas import Evidence
from ..schemas.core import sha256_text
from ..sources.websearch import WebHit, search_web
from .extract import load_prompt

PROMPT = "web.txt"
CLAIM_OPEN, CLAIM_CLOSE, HEAD_OPEN, HEAD_CLOSE = "<<<CLAIM", "CLAIM>>>", "<<<HEADLINES", "HEADLINES>>>"
STANCES = ("reports_as_fact", "debunks", "mentions_only", "unrelated")
TIER_TRUSTED, TIER_OTHER = 2, 3
SOURCE_ID = "web_news_search"

SCHEMA = {
    "type": "OBJECT",
    "properties": {"items": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "headline": {"type": "INTEGER"}, "stance": {"type": "STRING", "enum": list(STANCES)}},
        "required": ["headline", "stance"]}}},
    "required": ["items"],
}


@dataclass
class WebResult:
    evidence: list[Evidence] = field(default_factory=list)
    hits: int = 0                                         # headlines found
    read: int = 0                                         # headlines the judge labelled
    supporting: list[str] = field(default_factory=list)  # distinct trusted outlets that report it as fact
    debunking: list[str] = field(default_factory=list)   # distinct trusted outlets or fact-checkers that debunk it
    error: str = ""
    stale: str = ""                                       # why agreeing coverage was not allowed to support the claim (old news worded as new)


def _worded_as_now(text: str, cfg: Config) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(t.casefold()) + r"(?!\w)", text.casefold()) for t in cfg.runtime.report.recent_terms)


def _is_forecast(text: str, cfg: Config) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(t.casefold()) + r"(?!\w)", text.casefold()) for t in cfg.runtime.web.forecast_terms)


def _clean(text: str) -> str:
    for marker in (CLAIM_OPEN, CLAIM_CLOSE, HEAD_OPEN, HEAD_CLOSE):
        text = text.replace(marker, "")
    return " ".join(text.split())


def _norm(name: str) -> str:
    return " ".join(name.casefold().split())


def tier_of(outlet: str, cfg: Config) -> int:
    s = cfg.runtime.web
    trusted = {_norm(n) for n in [*s.reputable_outlets, *s.fact_checkers]}
    return TIER_TRUSTED if _norm(outlet) in trusted else TIER_OTHER


def is_fact_checker(outlet: str, cfg: Config) -> bool:
    return _norm(outlet) in {_norm(n) for n in cfg.runtime.web.fact_checkers}


def _evidence(hit: WebHit, role: str, tier: int, model: str, template_sha: str, now: datetime, stance: str) -> Evidence:
    when = hit.published_at.date().isoformat() if hit.published_at else "undated"
    passage = f"{hit.title} ({hit.outlet}, {when})"
    return Evidence(role=role, route="web", source_id=SOURCE_ID, url=hit.url, fetched_at=now, tier=tier,  # type: ignore[arg-type]
                    passage=passage, passage_sha256=sha256_text(passage), model=model, model_sha256=template_sha,
                    publisher=hit.outlet, rating=stance.replace("_", " "), reviewed_claim=hit.title, reviewed_at=hit.published_at)


def label_hits(client: GeminiClient, claim_text: str, hits: list[WebHit], now: datetime) -> dict[int, str]:
    template = load_prompt(PROMPT)
    listing = "\n".join(f"{i}. {h.outlet} | {h.published_at.date().isoformat() if h.published_at else 'undated'} | {_clean(h.title)}" for i, h in enumerate(hits, start=1))
    prompt = template.replace("{today}", now.date().isoformat()).replace("{claim}", _clean(claim_text)).replace("{headlines}", listing)
    items = client.generate_json(prompt, SCHEMA).data.get("items")
    if not isinstance(items, list):
        raise SourceError("gemini_llm", "BadOutput")
    out: dict[int, str] = {}
    for item in items:
        n, stance = (item.get("headline"), item.get("stance")) if isinstance(item, dict) else (None, None)
        if isinstance(n, int) and 1 <= n <= len(hits) and stance in STANCES:
            out.setdefault(n - 1, stance)
    return out


def check_web(client: GeminiClient, cfg: Config, claim_text: str, http: httpx.Client | None = None, now: datetime | None = None,
              search=search_web) -> WebResult:
    s = cfg.runtime.web
    now = now or datetime.now(timezone.utc)
    out = WebResult()
    try:
        hits = search(cfg, claim_text, http)
    except (httpx.HTTPError, ValueError) as exc:
        out.error = type(exc).__name__
        return out
    out.hits = len(hits)
    hits = hits[: s.max_hits_read]
    if not hits:
        return out
    try:
        labels = label_hits(client, claim_text, hits, now)
    except SourceError as exc:
        out.error = type(exc).__name__
        return out
    out.read = len(labels)
    template_sha = sha256_text(load_prompt(PROMPT))
    pro = [(hits[i], tier_of(hits[i].outlet, cfg)) for i, st in labels.items() if st == "reports_as_fact"]
    con = [(hits[i], tier_of(hits[i].outlet, cfg)) for i, st in labels.items() if st == "debunks"]
    pro_outlets = {_norm(h.outlet): h.outlet for h, t in pro if t == TIER_TRUSTED}
    con_outlets = {_norm(h.outlet): h.outlet for h, t in con if t == TIER_TRUSTED}
    fact_check_debunk = any(is_fact_checker(h.outlet, cfg) for h, t in con if t == TIER_TRUSTED)
    support_ok = len(pro_outlets) >= s.min_outlets_support
    if support_ok and _is_forecast(claim_text, cfg):
        support_ok = False                             # outlets report forecasts as news, which does not make the forecast true (dev row u24)
        out.stale = "the claim is a forecast about the future: outlets reporting the forecast do not confirm it, so it is not counted as support"
    if support_ok and _worded_as_now(claim_text, cfg):
        newest = max((h.published_at for h, t in pro if t == TIER_TRUSTED and h.published_at), default=None)
        if newest is None or (now - newest).days > cfg.runtime.report.stale_days:
            support_ok = False                         # coverage of an old event cannot confirm "tonight": found by test row r06 (2016 demonetisation worded as new)
            out.stale = ("the claim is worded as happening now, but the newest agreeing coverage is "
                         + ("undated" if newest is None else f"dated {newest.date().isoformat()}") + ": it may be an old story shared as new, so it is not counted as support")
    debunk_ok = fact_check_debunk or len(con_outlets) >= s.min_outlets_debunk
    out.supporting = sorted(pro_outlets.values()) if support_ok else []
    out.debunking = sorted(con_outlets.values()) if debunk_ok else []
    seen: set[tuple[str, str]] = set()
    for hit, tier in [*pro, *con]:
        stance = "reports_as_fact" if (hit, tier) in pro else "debunks"
        key = (_norm(hit.outlet), stance)
        if key in seen:
            continue                                   # one record per outlet and stance: a second copy of the same story adds nothing
        seen.add(key)
        decisive = tier == TIER_TRUSTED and (support_ok if stance == "reports_as_fact" else debunk_ok)
        role = ("supports" if stance == "reports_as_fact" else "contradicts") if decisive else "context"
        out.evidence.append(_evidence(hit, role, tier, client.model, template_sha, now, stance))
    mentions = [hits[i] for i, st in labels.items() if st == "mentions_only"][: s.max_cited]
    out.evidence.extend(_evidence(h, "context", tier_of(h.outlet, cfg), client.model, template_sha, now, "mentions only") for h in mentions)
    return out
