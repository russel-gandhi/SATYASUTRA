"""Print real posts from the live Bluesky stream, then a rate summary. Exit 0 only if at least one post arrived.

    python scripts/tail_posts.py --seconds 20
    python scripts/tail_posts.py --seconds 60 --show 0 --write-rate     # measure only, write docs/ingest_rate.md
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.ingest import IngestStats, author_salt, stream_posts  # noqa: E402

RATE_DOC = ROOT / "docs" / "ingest_rate.md"
PREVIEW_CHARS = 110


async def tail(seconds: float, show: int, stats: IngestStats) -> int:
    cfg = load_config().runtime.ingest
    shown = 0
    deadline = time.monotonic() + seconds

    async def consume():
        nonlocal shown
        async for post in stream_posts(cfg, stats, author_salt()):
            if shown < show:
                shown += 1
                text = " ".join(post.text.split())[:PREVIEW_CHARS]
                print(f"{post.posted_at:%H:%M:%S} [{post.lang}] {text}")

    try:
        await asyncio.wait_for(consume(), max(0.1, deadline - time.monotonic()))
    except asyncio.TimeoutError:
        pass
    return stats.posts


def render_rate(stats: IngestStats, seconds: float, stamp: str, cfg) -> str:
    skipped = ", ".join(f"{k}: {v}" for k, v in sorted(stats.skipped.items())) or "none"
    return "\n".join([
        "# Live ingest rate (ticket F1-04)",
        "",
        f"Measured {stamp} for {seconds:.0f} s from the live Bluesky Jetstream, filters from `config/runtime.yaml` "
        f"(languages {cfg.langs or 'all'}, minimum {cfg.min_text_chars} characters, replies {'skipped' if cfg.skip_replies else 'kept'}).",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Events received | {stats.events} ({stats.events / seconds:.1f} per second) |",
        f"| Posts passing the filters | {stats.posts} ({stats.posts / seconds:.2f} per second, {stats.posts / seconds * 60:.0f} per minute) |",
        f"| Skipped, by reason | {skipped} |",
        f"| Malformed | {stats.malformed} |",
        f"| Reconnects | {stats.reconnects} |",
        "",
        "One run on one network, at one time of day: treat it as an order of magnitude, not a constant.",
        "",
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--show", type=int, default=15, help="how many posts to print")
    ap.add_argument("--write-rate", action="store_true", help=f"write {RATE_DOC.relative_to(ROOT)}")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # posts contain emoji; a cp1252 console would crash
    load_env_file(ROOT / ".env")
    stats = IngestStats()
    started = time.monotonic()
    posts = asyncio.run(tail(args.seconds, args.show, stats))
    took = max(time.monotonic() - started, 0.001)
    print(f"-- {stats.events} events, {posts} posts in {took:.0f}s ({posts / took:.2f}/s); skipped {dict(sorted(stats.skipped.items()))}; malformed {stats.malformed}; reconnects {stats.reconnects}")
    if args.write_rate:
        RATE_DOC.write_text(render_rate(stats, took, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), load_config().runtime.ingest), encoding="utf-8")
        print(f"wrote {RATE_DOC.relative_to(ROOT)}")
    return 0 if posts else 1


if __name__ == "__main__":
    raise SystemExit(main())
