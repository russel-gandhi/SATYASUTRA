"""Choose the escalation floor from a live capture (ticket F2-05).

    python scripts/calibrate_escalation.py capture --seconds 300
    python scripts/calibrate_escalation.py write --value 0.6 --note "..."

`capture` runs the fast-clock worker on the live stream and then prints, for each candidate floor of distinct authors in the
velocity window, how many clusters would have been escalated and what share of posts that is. A person reads the clusters that
pass before choosing the floor: the floor is a policy, so the table informs it and does not pick it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.cache import TTLCache  # noqa: E402
from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.fastclock import FastClock, FastClockWorker  # noqa: E402

SECONDS_PER_MINUTE = 60
MAX_AUTHORS_SHOWN = 8
PREVIEW = 110
THRESHOLD_NAME = "escalation_min_authors_per_min"


async def capture(seconds: int) -> int:
    cfg = load_config()
    window = cfg.runtime.fastclock.window_s
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        fc = FastClock(cfg, TTLCache(Path(tmp) / "c.sqlite3"))
        worker = FastClockWorker(cfg, fc)
        worker.start()
        await asyncio.sleep(seconds)
        now, stats = worker._wall(), worker.stats()
        await worker.stop()
        clusters = list(fc.clusterer.clusters.values())
    per_cluster = [(len({a for _, a in c.stamps}), c) for c in clusters]
    print(f"{seconds}s live capture: {stats['posts_processed']} posts, {len(clusters)} clusters, window {window}s")
    print(f"\n{'distinct authors':>16s} {'= authors/min':>14s} {'clusters passing':>17s} {'posts in them':>14s} {'share of posts':>15s}")
    for k in range(1, MAX_AUTHORS_SHOWN + 1):
        passing = [c for n, c in per_cluster if n >= k]
        posts = sum(c.size for c in passing)
        print(f"{k:16d} {k / (window / SECONDS_PER_MINUTE):14.2f} {len(passing):17d} {posts:14d} {posts / max(stats['posts_processed'], 1):15.1%}")
    print("\nClusters with the most distinct authors (read these before choosing a floor):")
    for n, c in sorted(per_cluster, key=lambda x: (x[0], x[1].size), reverse=True)[:6]:
        print(f"  {n} authors, {c.size} posts: {c.rep_text[:PREVIEW]!r}")
    return 0


def write(value: float, note: str) -> int:
    target = ROOT / "config" / "thresholds.yaml"
    text = target.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    data["thresholds"] = (data.get("thresholds") or {}) | {THRESHOLD_NAME: {"value": value, "calibrated_on": note, "date": date.today().isoformat()}}
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    target.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"wrote {THRESHOLD_NAME}={value} to {target.relative_to(ROOT)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--seconds", type=int, required=True)
    w = sub.add_parser("write")
    w.add_argument("--value", type=float, required=True)
    w.add_argument("--note", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    return asyncio.run(capture(args.seconds)) if args.cmd == "capture" else write(args.value, args.note)


if __name__ == "__main__":
    raise SystemExit(main())
