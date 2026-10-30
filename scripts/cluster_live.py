"""Cluster the live Bluesky stream for a while and print the top clusters (ticket F1-08).

    python scripts/cluster_live.py --seconds 60

No model and no lookup is called here: this is the fast clock on its own.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.cache import TTLCache  # noqa: E402
from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.fastclock import FastClock  # noqa: E402
from satyasutra.ingest import IngestStats, author_salt, stream_posts  # noqa: E402

PREVIEW = 90


async def run(seconds: float):
    cfg = load_config()
    stats, n = IngestStats(), 0
    with tempfile.TemporaryDirectory() as tmp:
        fc = FastClock(cfg, TTLCache(Path(tmp) / "c.sqlite3"))
        started = time.perf_counter()

        async def consume():
            nonlocal n
            async for post in stream_posts(cfg.runtime.ingest, stats, author_salt()):
                fc.process(post)
                n += 1

        try:
            await asyncio.wait_for(consume(), seconds)
        except asyncio.TimeoutError:
            pass
        took = time.perf_counter() - started
        window = fc.settings.window_s
        rows = [(c, v, c.author_velocity(fc._clock(), window)) for c, v in fc.top()]
        return fc, stats, n, took, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=60)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    fc, stats, n, took, rows = asyncio.run(run(args.seconds))
    window_min = fc.settings.window_s / 60
    print(f"{n} posts in {took:.0f}s into {len(fc.clusterer.clusters)} clusters; top {len(rows)} by distinct authors per minute (window {window_min:g} min):")
    for c, posts_pm, authors_pm in rows:
        text = " ".join(c.rep_text.split())[:PREVIEW]
        print(f"  size {c.size:3d}  posts/min {posts_pm:5.2f}  authors/min {authors_pm:5.2f}  {text}")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
