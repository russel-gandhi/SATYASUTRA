"""Run the fast-clock worker on the live Bluesky stream and print its counters and top clusters (ticket F2-04).

    python scripts/worker_live.py --seconds 120
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.cache import TTLCache  # noqa: E402
from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.fastclock import FastClock, FastClockWorker  # noqa: E402

REPORT_EVERY_S = 30


async def run(seconds: int) -> None:
    cfg = load_config()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        worker = FastClockWorker(cfg, FastClock(cfg, TTLCache(Path(tmp) / "c.sqlite3")))
        worker.start()
        elapsed = 0
        while elapsed < seconds:
            step = min(REPORT_EVERY_S, seconds - elapsed)
            await asyncio.sleep(step)
            elapsed += step
            s = worker.stats()
            print(f"[{elapsed:4d}s] processed {s['posts_processed']}, {s['posts_per_minute']}/min, p95 {s['process_ms_p95']} ms, "
                  f"clusters {s['clusters_now']}, dropped {s['posts_dropped']}, repeats {s['exact_repeats']}, reconnects {s['stream_reconnects']}", flush=True)
        print("\ntop clusters (by distinct authors per minute):")
        for c in worker.top():
            print(f"  size {c['size']:3d}  {c['authors_per_minute']:5.2f} authors/min  {c['posts_per_minute']:5.2f} posts/min  {c['example_url']}")
            print(f"       {c['example_text'][:100]!r}")
        print("\nfinal stats:", worker.stats())
        await worker.stop()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=int, required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    asyncio.run(run(args.seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
