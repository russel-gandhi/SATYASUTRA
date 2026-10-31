"""The officials' view: a triage list of what is spreading on the stream we watch, disaster-related topics first, each marked with what we
have already found out about it.

It ranks and labels; it never decides. A row's state comes only from a verdict we already made (the /verify cache) or says "not checked
yet". Topic is a plain word match against config lists (English and Hindi), not a model judgement, and a word list misses reworded posts.
"""

from __future__ import annotations

import re
from typing import Callable

from .config import Config

WORD = re.compile(r"\w+", re.UNICODE)
DISASTER = "disaster"
GENERAL = "general"


def topic_of(text: str, cfg: Config) -> str:
    """Disaster when the text contains a configured term as a whole word or phrase, else general."""
    low = text.casefold()
    for term in cfg.runtime.officials.disaster_terms:
        if re.search(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)", low):
            return DISASTER
    return GENERAL


def state_of(body: dict | None, cfg: Config) -> dict:
    """What we already know about a claim from a cached verdict: the most decisive label among its claims, or not checked yet."""
    if not body or not body.get("claims"):
        return {"checked": False, "label": None, "confidence_band": None, "ledger_seq": None}
    order = cfg.runtime.officials.label_priority
    best = min(body["claims"], key=lambda c: order.index(c["label"]) if c["label"] in order else len(order))
    return {"checked": True, "label": best["label"], "confidence_band": best["confidence_band"], "ledger_seq": best.get("ledger_seq")}


def build_view(clusters: list[dict], peek: Callable[[str], dict | None], cfg: Config) -> list[dict]:
    """Rows for the page: disaster topics first, then by authors per minute (distinct people, so one account repeating a template ranks low)."""
    rows = []
    for c in clusters:
        text = c["example_text"]
        rows.append({**c, "topic": topic_of(text, cfg), "state": state_of(peek(text), cfg)})
    rows.sort(key=lambda r: (r["topic"] != DISASTER, -r["authors_per_minute"], -r["size"]))
    return rows[:cfg.runtime.officials.max_rows]


def annotate(snap: dict, peek: Callable[[str], dict | None], cfg: Config) -> dict:
    """The India pulse for the page: each row gets a topic (disaster words first) and what we already know from a cached verdict. Disaster rows sort first within a lane."""
    def mark(rows: list[dict], text_of: Callable[[dict], str]) -> list[dict]:
        out = [{**r, "topic": topic_of(text_of(r), cfg), "state": state_of(peek(text_of(r)), cfg)} for r in rows]
        return sorted(out, key=lambda r: r["topic"] != DISASTER)
    return {"fetched_at": snap["fetched_at"], "sources": snap["sources"], "note": snap["note"], "headlines_read": snap["headlines_read"],
            "trending": mark(snap["trending"], lambda r: r["term"]), "rumours": mark(snap["rumours"], lambda r: r["text"]), "stories": mark(snap["stories"], lambda r: r["text"])}
