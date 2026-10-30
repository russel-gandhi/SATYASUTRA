"""Extract claims from real posts on the live stream with Gemini and print them next to the post (ticket F1-05).

    python scripts/extract_live.py --seconds 30

Calls are spaced to the free-tier limit in config/policy.yaml, so a 30 second run handles only a handful of posts.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.claims import extract_claims  # noqa: E402
from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.ingest import IngestStats, author_salt, stream_posts  # noqa: E402
from satyasutra.llm import GeminiClient  # noqa: E402

PREVIEW = 160


def clip(text: str) -> str:
    return " ".join(text.split())[:PREVIEW]


async def run(seconds: float) -> tuple[Counter, int]:
    cfg = load_config()
    client = GeminiClient(cfg)
    outcomes: Counter = Counter()
    deadline = time.monotonic() + seconds
    done = 0

    async def consume():
        nonlocal done
        async for post in stream_posts(cfg.runtime.ingest, IngestStats(), author_salt()):
            if time.monotonic() > deadline:
                return
            result = await asyncio.to_thread(extract_claims, client, cfg, post)      # the client blocks on the rate limiter
            done += 1
            outcomes[result.status] += 1
            print(f"\nPOST [{post.lang}]: {clip(post.text)}")
            print(f"  -> {result.status} (attempts {result.attempts}, tokens {result.tokens}){': ' + result.reason if result.reason else ''}")
            for c in result.claims:
                print(f"  CLAIM [{c.lang}] {c.text_en}   | quote span {c.span} time={c.time_ref} place={c.place_ref}")
            for n in result.non_claims:
                print(f"  {n.kind.upper()}: {clip(n.quote)}")

    await consume()
    return outcomes, done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=30)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    outcomes, done = asyncio.run(run(args.seconds))
    print(f"\n-- {done} posts extracted: {dict(outcomes)}")
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main())
