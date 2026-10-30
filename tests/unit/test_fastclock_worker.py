"""The fast-clock worker against a local websocket server speaking the Jetstream message shape (see test_jetstream.py)."""

import asyncio
import json
import time
from pathlib import Path

import pytest
from test_jetstream import ev
from websockets.asyncio.server import serve

from satyasutra.cache import TTLCache
from satyasutra.config import load_config
from satyasutra.fastclock import FastClock, FastClockWorker
from satyasutra.fastclock.worker import percentile
from satyasutra.ingest import hash_author

CONFIG = Path(__file__).resolve().parents[2] / "config"
SETTLE_S = 5.0


def text(n: int) -> str:
    """Posts that are NOT near-duplicates of each other, so each lands in its own cluster."""
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]
    return " ".join(f"{words[(n + i) % len(words)]}{n * 7 + i}" for i in range(12))


def config(port, **fastclock):
    cfg = load_config(CONFIG)
    ingest = cfg.runtime.ingest.model_copy(update={"ws_url": f"ws://127.0.0.1:{port}/subscribe", "reconnect_delay_s": 0.01, "idle_timeout_s": 5, "langs": []})
    fc = cfg.runtime.fastclock.model_copy(update=fastclock)
    return cfg.model_copy(update={"runtime": cfg.runtime.model_copy(update={"ingest": ingest, "fastclock": fc})})


def make_worker(tmp_path, cfg, **kw):
    return FastClockWorker(cfg, FastClock(cfg, TTLCache(tmp_path / "c.sqlite3")), salt="s", **kw)


async def until(predicate, timeout=SETTLE_S):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def run(coro):
    return asyncio.run(coro)


def test_counters_move_and_top_is_ordered_by_distinct_authors_per_minute(tmp_path):
    async def go():
        async def handler(ws):
            n = 0
            for author in range(5):                                   # one message spread by 5 different accounts
                n += 1
                await ws.send(ev(did=f"did:plc:a{author}", rkey=f"spread{author}", text=text(1), time_us=n))
            for i in range(8):                                        # one account repeating a different message
                n += 1
                await ws.send(ev(did="did:plc:bot", rkey=f"bot{i}", text=text(2), time_us=n))
            await ws.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            worker = make_worker(tmp_path, config(server.sockets[0].getsockname()[1]))
            worker.start()
            assert await until(lambda: worker.processed >= 13)
            stats, top = worker.stats(), worker.top()
            await worker.stop()
        return stats, top

    stats, top = run(go())
    assert stats["posts_received"] == 13 and stats["posts_processed"] == 13 and stats["posts_dropped"] == 0
    assert stats["clusters_now"] == 2 and stats["posts_per_minute"] > 0 and stats["process_ms_p95"] >= stats["process_ms_median"] > 0
    assert stats["stream_events"] == 13 and stats["last_post_age_s"] >= 0
    assert [c["size"] for c in top] == [5, 8], "five accounts outrank one account posting more often"
    assert top[0]["authors_per_minute"] > top[1]["authors_per_minute"] and top[1]["posts_per_minute"] > top[0]["posts_per_minute"]
    assert top[0]["example_url"].startswith("https://") and "did:plc:a0" in top[0]["example_url"]
    served = json.dumps(top)
    assert "author_hash" not in served and not any(hash_author("s", f"did:plc:a{i}") in served for i in range(5)), "author hashes are never served"


def test_exact_repeats_are_counted_but_still_join_their_cluster(tmp_path):
    async def go():
        async def handler(ws):
            for i in range(4):
                await ws.send(ev(did=f"did:plc:x{i}", rkey=f"r{i}", text="THE same sentence  repeated http://t.co/a by several people today", time_us=i + 1))
            await ws.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            worker = make_worker(tmp_path, config(server.sockets[0].getsockname()[1]))
            worker.start()
            assert await until(lambda: worker.processed >= 4)
            result = worker.stats(), worker.top()
            await worker.stop()
        return result

    stats, top = run(go())
    assert stats["exact_repeats"] == 3 and top[0]["size"] == 4


def test_a_slow_processor_makes_the_oldest_posts_drop_and_every_post_is_accounted_for(tmp_path):
    total = 60

    async def go():
        async def handler(ws):
            for i in range(total):
                await ws.send(ev(did=f"did:plc:s{i}", rkey=f"r{i}", text=text(i), time_us=i + 1))
            await ws.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            cfg = config(server.sockets[0].getsockname()[1], queue_max=5)
            worker = make_worker(tmp_path, cfg, process=lambda post: time.sleep(0.02))
            worker.start()
            assert await until(lambda: worker.posts_in == total and not worker._queue)
            stats = worker.stats()
            await worker.stop()
        return stats

    stats = run(go())
    assert stats["posts_dropped"] > 0, "a processor slower than the stream must shed load"
    assert stats["posts_received"] == total
    assert stats["posts_processed"] + stats["posts_dropped"] == total and stats["queue_length"] == 0


def test_a_dropped_connection_reconnects_and_keeps_counting(tmp_path):
    connections = []

    async def go():
        async def handler(ws):
            connections.append(1)
            if len(connections) == 1:
                await ws.send(ev(did="did:plc:one", rkey="first", text=text(1), time_us=1))
                await ws.close()
                return
            await ws.send(ev(did="did:plc:two", rkey="second", text=text(2), time_us=2))
            await ws.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            worker = make_worker(tmp_path, config(server.sockets[0].getsockname()[1]))
            worker.start()
            assert await until(lambda: worker.processed >= 2)
            stats = worker.stats()
            await worker.stop()
        return stats

    stats = run(go())
    assert stats["stream_reconnects"] >= 1 and stats["posts_processed"] == 2 and stats["clusters_now"] == 2


def test_stopping_the_worker_leaves_no_task_behind(tmp_path):
    async def go():
        async def handler(ws):
            await ws.send(ev(text=text(1), time_us=1))
            await ws.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            before = len(asyncio.all_tasks())
            worker = make_worker(tmp_path, config(server.sockets[0].getsockname()[1]))
            worker.start()
            assert await until(lambda: worker.processed >= 1 and worker.running)
            await worker.stop()
            await asyncio.sleep(0.05)
            return before, len(asyncio.all_tasks()), worker.running

    before, after, running = run(go())
    assert after <= before and running is False


def test_idle_clusters_are_counted_when_they_are_dropped(tmp_path):
    cfg = load_config(CONFIG)
    now = [1000.0]
    clock = FastClock(cfg, TTLCache(tmp_path / "c.sqlite3"), clock=lambda: now[0])
    from test_fastclock import post  # noqa: PLC0415  (a Post builder from the fast-clock tests)
    clock.process(post("a distinct story about a bridge collapse in the capital this morning", 1))
    now[0] += cfg.runtime.fastclock.cluster_idle_s + 1
    clock.process(post("a different story about floods in a coastal town overnight and rescues", 2))
    assert clock.clusterer.dropped_total == 1 and len(clock.clusterer.clusters) == 1


def test_percentile_is_nearest_rank_and_safe_on_empty():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert percentile(values, 0.5) == 5.0 and percentile(values, 0.95) == 10.0 and percentile([], 0.5) is None
    assert percentile([7.0], 0.95) == 7.0


def test_post_url_handles_non_bluesky_ids(tmp_path):
    cfg = load_config(CONFIG)
    worker = make_worker(tmp_path, cfg)
    assert worker.post_url("at://did:plc:abc/app.bsky.feed.post/3k") == "https://bsky.app/profile/did:plc:abc/post/3k"
    assert worker.post_url("api_text:abcdef") is None and worker.post_url("at://broken") is None


# the endpoints and the app lifecycle ---------------------------------------------------------------------------------

def app_with(tmp_path, posts, **kw):
    from fastapi import FastAPI  # noqa: F401
    from satyasutra import __version__
    from satyasutra.api.app import create_app
    from satyasutra.ingest import IngestStats, parse_event
    from satyasutra.ledger import SQLiteLedgerStore
    from satyasutra.verdict_cache import VerdictCache

    cfg = load_config(CONFIG)
    ingest = cfg.runtime.ingest.model_copy(update={"langs": []})

    async def stream(_cfg, stats, salt):
        for raw in posts:
            yield parse_event(raw, ingest, salt, stats)       # the real parser on real-shaped events
        await asyncio.Event().wait()                          # then stay connected and quiet, like a live stream between posts

    worker = make_worker(tmp_path, cfg, stream=stream)
    store = SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__)
    app = create_app(cfg, store, worker=worker, verdict_cache=VerdictCache(TTLCache(tmp_path / "v.sqlite3"), cfg, __version__), **kw)
    return app, worker


def test_endpoints_serve_live_clusters_and_counters_and_the_worker_stops_with_the_app(tmp_path):
    from fastapi.testclient import TestClient

    raws = [ev(did=f"did:plc:u{i}", rkey=f"r{i}", text=text(1), time_us=i + 1) for i in range(3)]
    app, worker = app_with(tmp_path, raws)
    with TestClient(app) as client:
        deadline = time.monotonic() + SETTLE_S
        while time.monotonic() < deadline and client.get("/fastclock/stats").json()["posts_processed"] < 3:
            time.sleep(0.02)
        stats = client.get("/fastclock/stats").json()
        clusters = client.get("/fastclock/top").json()["clusters"]
        assert stats["running"] is True and stats["posts_processed"] == 3 and stats["clusters_now"] == 1
        assert clusters[0]["size"] == 3 and clusters[0]["example_url"].startswith("https://bsky.app/profile/")
    assert worker.running is False


def test_a_verify_request_counts_toward_the_same_clusters_as_the_stream(tmp_path):
    from fastapi.testclient import TestClient
    from test_verify_api import POST_TEXT, gemini_reply, item  # noqa: F401

    app, worker = app_with(tmp_path, [ev(did="did:plc:one", rkey="r1", text=POST_TEXT, time_us=1)],
                           extract=lambda p: __import__("satyasutra.claims", fromlist=["ExtractionResult"]).ExtractionResult("no_items"),
                           lookup=lambda t, l: None)
    with TestClient(app) as client:
        deadline = time.monotonic() + SETTLE_S
        while time.monotonic() < deadline and worker.processed < 1:
            time.sleep(0.02)
        assert client.post("/verify", json={"text": POST_TEXT}).status_code == 200
        assert worker.top()[0]["size"] == 2, "the /verify request joined the cluster the stream created"


def test_the_endpoints_say_so_when_the_worker_is_not_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from satyasutra import __version__
    from satyasutra.api.app import create_app
    from satyasutra.ledger import SQLiteLedgerStore

    cfg = load_config(CONFIG)
    assert cfg.runtime.fastclock.worker_enabled is False, "the shipped default must let the API start with no network"
    client = TestClient(create_app(cfg, SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__)))
    assert client.get("/fastclock/top").status_code == 503 and client.get("/fastclock/stats").status_code == 503
