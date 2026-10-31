"""Save today's official releases (the same RBI and SEBI feeds the system reads) as a snapshot, so the T and C claims stay valid.

    python scripts/eval/build_official.py

Why: the feeds only hold the newest items, so a claim built from today's release cannot be checked against tomorrow's feed. The
snapshot keeps the exact passages (as the system's own parser makes them). The harness loads it into a private index, so a run
measures the claims against the text they were written from. Running this again later only adds items; it never removes any.
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import SNAPSHOT, read_jsonl, write_jsonl  # noqa: E402
from satyasutra.config import load_config  # noqa: E402
from satyasutra.sources.official import parse_feed  # noqa: E402
from satyasutra.sources.probe import build_user_agent  # noqa: E402


def main() -> int:
    cfg = load_config()
    s = cfg.runtime.official
    now = datetime.now(timezone.utc)
    have = {r["sha256"]: r for r in read_jsonl(SNAPSHOT)}
    added = 0
    headers = {"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}
    with httpx.Client(timeout=s.timeout_s, headers=headers, follow_redirects=True) as http:
        for feed in s.feeds:
            try:
                body = http.get(feed.url).content
            except httpx.HTTPError as exc:
                print(f"{feed.id}: unreachable ({type(exc).__name__})")
                continue
            for p in parse_feed(body, feed, cfg, now)[: s.max_items_per_feed]:
                if p.sha256 in have:
                    continue
                row = asdict(p)
                row["published_at"] = p.published_at.isoformat() if p.published_at else None
                row["fetched_at"] = p.fetched_at.isoformat()
                have[p.sha256] = row
                added += 1
            print(f"{feed.id}: ok")
    write_jsonl(SNAPSHOT, sorted(have.values(), key=lambda r: (r["source_id"], r["published_at"] or "", r["sha256"])))
    print(f"snapshot has {len(have)} passages ({added} new) -> {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
