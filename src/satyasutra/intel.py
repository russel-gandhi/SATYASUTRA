"""Views for the investigator side: overview, claim intelligence, media lab, one big evidence graph, case timeline and the source registry.

Every number and row here is computed from records this instance stored (records.py) or from the indexes it holds. It is not nationwide surveillance and the
pages say so. A claim is "the same underlying claim" as another when their English texts overlap above `intel.same_claim_jaccard`; because every claim is
translated to English first, the same claim sent in Hindi and in English lands in one group. That threshold is a policy choice, not calibrated.
"""

from __future__ import annotations

from collections import Counter

from .config import Config
from .fastclock.normalise import normalise
from .report import build_graph
from .scam import load_domains
from .sources.index import OfficialIndex

MEDIA_KINDS = ("media", "video", "audio")
DAY = 10                         # characters of an ISO timestamp that make the date


def claims_of(records: list[dict]) -> list[tuple[dict, dict]]:
    """(claim, record) pairs from claim records."""
    return [(c, r) for r in records if r["kind"] == "claims" for c in r["body"]["body"]["claims"]]


def summarise(record: dict) -> dict:
    """One line for a history list: what was submitted and how it came out. No thumbnails or evidence bodies."""
    b, kind = record["body"], record["kind"]
    out = {"id": record["id"], "kind": kind, "created_at": record["created_at"], "title": "", "verdicts": [], "language": None}
    if kind == "claims":
        cs = b["body"]["claims"]
        out.update(title=b["text"][:160], verdicts=[c["label"] for c in cs], language=(cs[0].get("lang") if cs else None))
    elif kind in ("media", "video", "audio"):
        res = b.get("result") or b
        out.update(title=b.get("name") or kind, verdicts=[res.get("label")] if res.get("label") else [])
    else:
        out.update(title=(b.get("name") or b.get("text") or kind)[:160])
    return out


def group_claims(records: list[dict], cfg: Config) -> list[dict]:
    """Same underlying claim across submissions: greedy grouping by word overlap of the English claim text."""
    cut = cfg.runtime.intel.same_claim_jaccard
    groups: list[dict] = []
    for c, r in sorted(claims_of(records), key=lambda p: p[1]["created_at"]):
        words = set(normalise(c["text"]).split())
        home = next((g for g in groups if words and len(words & g["words"]) / len(words | g["words"]) >= cut), None)
        if home is None:
            home = {"words": set(words), "text": c["text"], "first_seen": r["created_at"], "records": [], "languages": Counter(), "verdicts": Counter(), "sources": {}, "media": []}
            groups.append(home)
        home["words"] |= words
        home["records"].append(r["id"])
        home["languages"][c.get("lang") or "?"] += 1
        home["verdicts"][c["label"]] += 1
        home["latest_verdict"], home["latest_at"] = c["label"], r["created_at"]
        for e in c.get("citations", []):
            home["sources"].setdefault(e["url"], {"publisher": e.get("publisher") or e["source_id"], "url": e["url"], "tier": e["tier"], "role": e["role"], "title": e.get("reviewed_claim") or ""})
    out = []
    for g in groups:
        srcs = list(g["sources"].values())
        out.append({"text": g["text"], "first_seen": g["first_seen"], "investigations": len(g["records"]), "records": g["records"], "languages": dict(g["languages"]),
                    "verdict": g["latest_verdict"], "verdict_counts": dict(g["verdicts"]),
                    "supporting": [s for s in srcs if s["role"] == "supports"], "contradicting": [s for s in srcs if s["role"] == "contradicts"], "context": [s for s in srcs if s["role"] == "context"]})
    out.sort(key=lambda g: (-g["investigations"], g["first_seen"]))
    return out


def overview(records: list[dict], cfg: Config) -> dict:
    claims = claims_of(records)
    verdicts = Counter(c["label"] for c, _ in claims)
    langs = Counter((c.get("lang") or "?") for c, _ in claims)
    sources = Counter()
    for c, _ in claims:
        for e in c.get("citations", []):
            sources[(e.get("publisher") or e["source_id"], e["tier"])] += 1
    per_day = Counter(r["created_at"][:DAY] for r in records)
    media = [r for r in records if r["kind"] in MEDIA_KINDS]
    needs_review = [summarise(r) for r in media if (r["body"].get("result") or r["body"]).get("label") not in (None, "INCONCLUSIVE")]
    groups = group_claims(records, cfg)
    return {
        "note": "Computed from the investigations stored on this server. It is not live or nationwide data.",
        "totals": {"investigations": len(records), "claims": len(claims), "claim_groups": len(groups), "media": len(media), "by_kind": dict(Counter(r["kind"] for r in records))},
        "verdicts": dict(verdicts), "languages": dict(langs), "per_day": dict(sorted(per_day.items())),
        "sources": [{"publisher": p, "tier": t, "count": n} for (p, t), n in sources.most_common(15)],
        "media_needing_review": needs_review, "recent": [summarise(r) for r in records[: cfg.runtime.intel.recent_n]],
        "geography": "No location data is collected, so there is no map. A map would only be honest where claims carry a verified place.",
    }


def media_lab(records: list[dict]) -> list[dict]:
    out = []
    for r in records:
        if r["kind"] not in MEDIA_KINDS:
            continue
        b = r["body"]
        res = b.get("result") or b
        out.append({"id": r["id"], "kind": r["kind"], "created_at": r["created_at"], "name": b.get("name", ""), "label": res.get("label"), "reasons": res.get("reasons", []),
                    "thumb": b.get("thumb"), "metadata": b.get("metadata", []), "forensics": res.get("forensics"), "matches": res.get("matches", []),
                    "frames": res.get("frames"), "explanation": res.get("explanation"), "note": b.get("note", "")})
    return out


def global_graph(records: list[dict]) -> dict:
    """All investigations in one graph. Source and entity ids are content-based, so a source cited by two investigations is one node with two edges."""
    claims = [c for c, _ in claims_of(records)]
    return build_graph(records, claims)


def registry(cfg: Config, official: OfficialIndex) -> dict:
    s, web = cfg.runtime.intel, cfg.runtime.web
    counts = official.publishers()
    latest = official.recent(s.registry_items)
    domains = load_domains()
    return {
        "official_feeds": [{"publisher": f.publisher, "tier": f.tier, "feed": f.url, "items_held": counts.get(f.publisher, 0)} for f in cfg.runtime.official.feeds],
        "latest_items": [{"publisher": p.publisher, "title": p.title, "url": p.url, "published_at": p.published_at.isoformat() if p.published_at else "", "sha256": p.sha256} for p in latest],
        "official_domains": [{"domain": d.domain, "owner": d.owner, "category": d.category} for d in domains.domains],
        "reputable_outlets": web.reputable_outlets, "fact_checkers": web.fact_checkers,
        "not_available": s.not_available,
        "note": "Only the latest items of each feed are held, not a full archive. Tier 1 is an official source. The domain list is used to check link names only; those sites are not read.",
    }


def case_view(case: dict, records: list[dict], cfg: Config) -> dict:
    """A case's timeline (its records in time order), its notes and one graph over its records. It points at records; deleted records are reported as missing."""
    held = {r["id"]: r for r in records}
    mine = sorted((held[i] for i in case["record_ids"] if i in held), key=lambda r: r["created_at"])
    return {**case, "missing": [i for i in case["record_ids"] if i not in held], "timeline": [summarise(r) for r in mine], "graph": global_graph(mine),
            "claims": group_claims(mine, cfg)}
