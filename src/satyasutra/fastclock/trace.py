"""Origin and spread of one cluster: who posted it first, how it mutated, how fast it reached new accounts.

Built only from the posts this server has seen on the live stream since it started, so it describes Bluesky and nothing else, and the
result says so. The earliest posts of a cluster are kept (the origin matters most); when a cluster outgrows the cap, later posts are
counted but not stored, and the trace reports that. Accounts appear only as "account 1, 2, ..." in order of first appearance: author
hashes never leave the server (Bible D27).
"""

from __future__ import annotations

import difflib
from datetime import datetime, timezone
from typing import Callable

from ..config import FastClockSettings
from .cluster import Cluster
from .normalise import normalise, text_key

SCOPE_NOTE = ("Built from the Bluesky posts this server has seen since it started, not from every post and not from other platforms. "
              "The first post here is the first one we saw, which is not necessarily the true origin.")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def word_diff(before: str, after: str) -> list[dict]:
    """Words removed and added between two versions of a text, in order."""
    a, b = before.split(), after.split()
    ops = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag in ("delete", "replace"):
            ops.append({"op": "removed", "text": " ".join(a[i1:i2])})
        if tag in ("insert", "replace"):
            ops.append({"op": "added", "text": " ".join(b[j1:j2])})
    return ops


def build_trace(cluster: Cluster, now: float, s: FastClockSettings, post_url: Callable[[str], str | None]) -> dict:
    members = cluster.members
    first = members[0] if members else None
    span = max(cluster.last_seen - cluster.first_seen, 0.0)

    timeline = []
    for b in range(s.trace_buckets):
        upto = cluster.first_seen + span * (b + 1) / s.trace_buckets
        seen = [m for m in members if m["t"] <= upto]
        timeline.append({"at_s": round(upto - cluster.first_seen, 1), "posts": len(seen), "accounts": len({m["account"] for m in seen})})

    variants: list[dict] = []
    index: dict[str, dict] = {}
    for m in members:
        key = text_key(normalise(m["text"]))
        if key in index:
            index[key]["posts"] += 1
            continue
        v = {"first_seen_s": round(m["t"] - cluster.first_seen, 1), "posts": 1, "text": m["text"], "url": post_url(m["post_id"]),
             "diff_from_previous": word_diff(variants[-1]["text"], m["text"]) if variants else []}
        index[key] = v
        variants.append(v)

    accounts = {m["account"] for m in members}
    return {
        "cluster_id": cluster.id,
        "first_post": {"text": first["text"], "url": post_url(first["post_id"]), "seen_at": _iso(first["t"]), "account": first["account"],
                       "posted_at": first["posted_at"]} if first else None,
        "posts_seen": cluster.size,
        "posts_stored": len(members),
        "stored_all_posts": len(members) >= cluster.size,
        "distinct_accounts_stored": len(accounts),
        "first_seen": _iso(cluster.first_seen),
        "last_seen": _iso(cluster.last_seen),
        "span_s": round(span, 1),
        "timeline": timeline,
        "variants": variants[:s.variants_shown],
        "variants_total": len(variants),
        "scope_note": SCOPE_NOTE,
    }
