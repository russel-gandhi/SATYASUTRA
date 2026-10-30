"""The fast-clock worker on the real Bluesky Jetstream. Run with `pytest -m contract`."""

import asyncio
from pathlib import Path

import pytest

from satyasutra.cache import TTLCache
from satyasutra.config import load_config
from satyasutra.envfile import load_env_file
from satyasutra.fastclock import FastClock, FastClockWorker

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]
WINDOW_S = 20
MIN_POSTS = 20


def test_the_worker_processes_the_live_stream_reports_sane_counters_and_stops_cleanly(tmp_path):
    load_env_file(ROOT / ".env")
    cfg = load_config()

    async def go():
        before = len(asyncio.all_tasks())
        worker = FastClockWorker(cfg, FastClock(cfg, TTLCache(tmp_path / "c.sqlite3")))
        worker.start()
        await asyncio.sleep(WINDOW_S)
        stats, top = worker.stats(), worker.top()
        await worker.stop()
        await asyncio.sleep(0.05)
        return before, len(asyncio.all_tasks()), stats, top

    before, after, stats, top = asyncio.run(go())
    print(f"live {WINDOW_S}s: {stats['posts_processed']} processed, {stats['posts_per_minute']}/min, p95 {stats['process_ms_p95']} ms, "
          f"{stats['clusters_now']} clusters, dropped {stats['posts_dropped']}, repeats {stats['exact_repeats']}")
    assert stats["error"] == "" and stats["posts_processed"] >= MIN_POSTS
    assert stats["posts_received"] == stats["posts_processed"] + stats["posts_dropped"] + stats["queue_length"]
    assert stats["process_ms_p95"] is not None and stats["clusters_now"] > 0
    assert [c["authors_per_minute"] for c in top] == sorted((c["authors_per_minute"] for c in top), reverse=True)
    assert after <= before
