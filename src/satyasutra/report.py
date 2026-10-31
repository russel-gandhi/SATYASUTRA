"""The Trust Report and its Evidence Graph, built from investigation records the server itself produced (records.py).

The report has the ten sections of the plan: what was submitted, language and entities, media findings, supporting evidence, contradicting evidence, source
credibility, contextual inconsistencies, the relationship graph, a verdict per claim, and the confidence and limitations. Nothing in it is written by a model:
every line is copied from a stored record, so it can only cite what was actually retrieved.

The graph holds only relationships that were established in this investigation: a source supports or contradicts a claim, a claim mentions an entity, an image
matches a debunked image, a claim matches a cluster spreading on the stream we watch. It is not a map of the internet and says so.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .config import Config
from .schemas.core import sha256_text

ROLE_EDGE = {"supports": "supports", "contradicts": "contradicts", "context": "context"}
ROUTE_KIND = {"factcheck": "factcheck", "web": "news", "contrast": "official", "provenance": "provenance"}


def _has(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(phrase.casefold()) + r"(?!\w)", text.casefold()) is not None


def _day(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def context_findings(claim: dict, cfg: Config, now: datetime) -> list[dict]:
    """Does the claim present old news as new? Only one check exists: a claim worded as happening now whose newest matching coverage is old."""
    s = cfg.runtime.report
    out: list[dict] = []
    dated = sorted(d for d in (_day(c.get("reviewed_at")) for c in claim.get("citations", []) if c.get("route") in ("web", "factcheck")) if d)
    if not dated or not any(_has(claim["text"] + " " + (claim.get("quote") or ""), t) for t in s.recent_terms):
        return out
    newest, oldest = dated[-1], dated[0]
    age = (now - newest).days
    if age > s.stale_days:
        out.append({"kind": "possibly_recycled", "level": "warning",
                    "text": f"The claim is worded as if it is happening now, but the newest coverage we found is dated {newest.date().isoformat()} ({age} days ago) and the earliest {oldest.date().isoformat()}. It may be an old story shared as new."})
    return out


def build_graph(records: list[dict], claims: list[dict]) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(nid: str, kind: str, label: str, **extra) -> str:
        nodes.setdefault(nid, {"id": nid, "kind": kind, "label": label[:140], **extra})
        return nid

    def edge(a: str, b: str, relation: str, **extra) -> None:
        if not any(e["from"] == a and e["to"] == b and e["relation"] == relation for e in edges):
            edges.append({"from": a, "to": b, "relation": relation, **extra})

    for c in claims:
        cid = node("claim:" + c["claim_id"], "claim", c["text"], verdict=c["label"])
        for e in c.get("entities", []):
            edge(cid, node("entity:" + e["id"], "entity", e["label"], url=e["url"], description=e.get("description", "")), "mentions")
            if e.get("died"):
                edge(node("fact:" + e["id"] + ":died", "registry", f"Wikidata date of death: {e['died']}", url=e["url"]), "entity:" + e["id"], "records")
        for ev in c.get("citations", []):
            kind = ROUTE_KIND.get(ev.get("route"), "source")
            label = (ev.get("publisher") or ev.get("source_id") or "source") + (f": {ev['reviewed_claim']}" if ev.get("reviewed_claim") else "")
            sid = node("src:" + sha256_text(ev["url"] + ev.get("passage_sha256", ""))[:12], kind, label, url=ev["url"], tier=ev["tier"], publisher=ev.get("publisher", ""),
                       date=(ev.get("reviewed_at") or "")[:10])
            edge(sid, cid, ROLE_EDGE.get(ev["role"], "context"), rating=ev.get("rating", ""))
        for r in c.get("related", [])[:3]:
            sid = node("src:" + sha256_text(r["url"])[:12], "factcheck", (r.get("publisher") or "fact-check") + ": " + (r.get("reviewed_claim") or ""), url=r["url"], tier=2)
            edge(sid, cid, "related_not_used", why=r.get("why_not_used", ""))
    for rec in records:
        body = rec["body"]
        if rec["kind"] == "claims" and body.get("spread"):
            m = body["spread"]
            for c in body["body"]["claims"]:
                edge("claim:" + c["claim_id"], node("cluster:" + str(m["cluster_id"]), "cluster", f"Spreading on Bluesky: {m['posts']} posts, {m['distinct_accounts']} accounts"), "spreading_as")
        if rec["kind"] in ("media", "video"):
            r = body["result"]
            mid = node("media:" + rec["id"], "media", body.get("name") or ("Uploaded " + rec["kind"]), verdict=r.get("label", ""))
            matches = r.get("matches", []) if rec["kind"] == "media" else [m for f in r.get("frames", []) for m in f.get("matches", [])]
            for m in matches:
                edge(mid, node("art:" + sha256_text(m["article_url"])[:12], "factcheck", f"{m.get('publisher', '')}: {m.get('title', '')}", url=m["article_url"], tier=2), "same_image_as", distance=m.get("distance"))
            for c in claims:
                if body.get("about_claims"):
                    edge(mid, "claim:" + c["claim_id"], "submitted_with")
    return {"nodes": list(nodes.values()), "edges": edges,
            "note": "Only relationships found in this investigation are shown. It is not a map of the wider internet."}


def build_report(records: list[dict], cfg: Config, lang: str = "en", now: datetime | None = None) -> dict:
    s = cfg.runtime.report
    now = now or datetime.now(timezone.utc)
    lang = lang if lang in s.verdict_names else "en"
    claims = [c for r in records if r["kind"] == "claims" for c in r["body"]["body"]["claims"]]
    names, tiers = s.verdict_names[lang], s.tier_names[lang]
    sections: dict = {"submitted": [], "media": [], "claims": [], "context": []}
    for r in records:
        b = r["body"]
        if r["kind"] == "claims":
            sections["submitted"].append({"kind": "text", "text": b["text"], "record": r["id"]})
        elif r["kind"] in ("media", "video"):
            sections["submitted"].append({"kind": r["kind"], "name": b.get("name", ""), "thumb": b.get("thumb"), "record": r["id"]})
            res = b["result"]
            sections["media"].append({"record": r["id"], "kind": r["kind"], "label": res.get("label"), "reasons": res.get("reasons", []), "forensics": res.get("forensics"),
                                      "matches": res.get("matches", []) if r["kind"] == "media" else [m for f in res.get("frames", []) for m in f.get("matches", [])],
                                      "metadata": b.get("metadata", []), "explanation": res.get("explanation"), "thumb": b.get("thumb")})
        elif r["kind"] == "voice":
            sections["submitted"].append({"kind": "voice", "text": b["text"], "language": b.get("language"), "record": r["id"]})
        elif r["kind"] == "audio":
            sections["media"].append({"record": r["id"], "kind": "voice", "label": b.get("label"), "reasons": b.get("reasons", []), "note": b.get("note", ""), "forensics": None, "matches": []})
        elif r["kind"] in ("notice", "doc", "page"):
            sections["submitted"].append({"kind": r["kind"], "text": b.get("text", "")[:2000], "name": b.get("name", ""), "record": r["id"]})
            sections["context"].extend({**f, "record": r["id"]} for f in b.get("findings", []))
    for c in claims:
        ev = c.get("citations", [])
        label = c["label"]
        shown = names.get(label, label)
        if label == "UNVERIFIABLE_YET" and not ev:
            shown = s.insufficient_label[lang]
        findings = context_findings(c, cfg, now)
        sections["context"].extend({**f, "claim": c["claim_id"]} for f in findings)
        sections["claims"].append({
            "claim_id": c["claim_id"], "text": c["text"], "original": c.get("quote"), "language": c.get("lang"), "translated": "TRANSLATED" in c.get("flags", []),
            "entities": [{"label": e["label"], "description": e.get("description", ""), "url": e["url"]} for e in c.get("entities", [])],
            "verdict": label, "verdict_name": shown, "confidence": c["confidence_band"], "flags": c.get("flags", []), "reasons": c.get("reasons", []),
            "supporting": [x for x in ev if x["role"] == "supports"], "contradicting": [x for x in ev if x["role"] == "contradicts"],
            "context_sources": [x for x in ev if x["role"] == "context"],
            "sources": [{"publisher": x.get("publisher") or x.get("source_id"), "url": x["url"], "tier": x["tier"], "tier_name": tiers.get(str(x["tier"]), ""), "role": x["role"],
                         "rating": x.get("rating", ""), "date": (x.get("reviewed_at") or "")[:10]} for x in ev],
            "explanation": c.get("explanation"), "ledger_seq": c.get("ledger_seq")})
    report = {"version": 1, "generated_at": now.isoformat(), "lang": lang, "sections": sections, "graph": build_graph(records, claims),
              "limitations": s.limitations[lang] + [n for m in sections["media"] for n in ([m["note"]] if m.get("note") else [])],
              "records": [r["id"] for r in records]}
    return report
