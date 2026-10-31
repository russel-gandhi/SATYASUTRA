"""Throughput of the India pulse, measured live: how long a refresh takes, how many items each stage keeps, and how fast a pasted claim is matched.

    python scripts/bench_pulse.py --runs 3 --out docs/throughput.md

Nothing here is simulated: every number is read from the live feeds on the day of the run, so it changes with the news. The Bluesky stream
funnel is measured separately (docs/ingest_rate.md); this script does not start that worker.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.pulse import Pulse, cluster_stories  # noqa: E402
from satyasutra.sources.probe import build_user_agent  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MATCH_PROBES = 20


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default="docs/throughput.md")
    args = ap.parse_args()
    load_env_file(ROOT / ".env")
    cfg = load_config(ROOT / "config")
    s = cfg.runtime.pulse
    now = datetime.now(timezone.utc)

    refresh_s: list[float] = []
    snap: dict = {}
    for _ in range(args.runs):
        p = Pulse(cfg)
        t = time.perf_counter()
        snap = p.refresh(now)
        refresh_s.append(time.perf_counter() - t)

    per_feed = []
    with httpx.Client(timeout=s.timeout_s, headers={"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}, follow_redirects=True) as client:
        p = Pulse(cfg, client)
        for feed in [*s.headline_feeds, *s.fact_check_feeds]:
            t = time.perf_counter()
            _, rows, st = p._fetch(client, feed, now)
            per_feed.append((feed.id, st, len(rows), time.perf_counter() - t))

    read = sum(n for _, _, n, _ in per_feed)
    ok = sum(1 for _, st, _, _ in per_feed if st == "ok")
    stories = snap["stories"]
    multi = [x for x in stories if len(x.get("outlets") or []) >= 2] if stories and "outlets" in stories[0] else None

    p = Pulse(cfg)
    p._snap, p._at = snap, now
    probes = [r["text"] for r in snap["rumours"][:MATCH_PROBES]] or snap["_corpus"][:MATCH_PROBES]
    times = []
    for text in probes:
        t = time.perf_counter()
        p.snapshot(now) and p.match(text)
        times.append(time.perf_counter() - t)

    lines = [f"# Pulse throughput, measured {now:%Y-%m-%d %H:%M} UTC", "",
             f"Command: `python scripts/bench_pulse.py --runs {args.runs}`. Live feeds, this machine, one network. Numbers change with the news.", "",
             "## Refresh (all feeds, parallel)", "",
             f"- {args.runs} runs, seconds: " + ", ".join(f"{x:.1f}" for x in refresh_s) + f" (median {statistics.median(refresh_s):.1f})",
             f"- Workers: {s.workers}; cache: {s.refresh_s:.0f} s, so the page answers from memory between refreshes.", "",
             "## Funnel (last run)", "",
             "| Stage | Count |", "|---|---|",
             f"| Feeds configured (plus Google Trends) | {len(per_feed)} |",
             f"| Feeds that answered | {ok} |",
             f"| Items read from feeds (one pass) | {read} |",
             f"| Headlines kept (newer than {s.max_age_hours} h) | {snap['headlines_read']} |",
             f"| Fact-checker articles shown | {len(snap['rumours'])} |",
             f"| Trending terms | {len(snap['trending'])} |",
             f"| Story clusters | {len(stories)} |"]
    if multi is not None:
        lines.append(f"| Clusters carried by 2+ outlets | {len(multi)} |")
    lines += ["", "## Per feed (one fetch each, sequential)", "", "| Feed | Result | Items | Seconds |", "|---|---|---|---|"]
    lines += [f"| {fid} | {st} | {n} | {sec:.2f} |" for fid, st, n, sec in per_feed]
    lines += ["", "## Matching a pasted claim against the snapshot", "",
              f"- {len(times)} probes (latest fact-checker headlines used as claims): median {statistics.median(times) * 1000:.1f} ms, slowest {max(times) * 1000:.1f} ms.", "",
              "## Not measured", "",
              "- Behaviour under many simultaneous visitors (the cache means one refresh serves all, but this was not load-tested).",
              "- The Bluesky stream worker (off by default; see docs/ingest_rate.md for its earlier funnel).",
              "- Feeds from other networks or at other times of day."]
    out = ROOT / args.out
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
