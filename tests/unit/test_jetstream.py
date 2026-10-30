import asyncio
import json
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

from satyasutra.config import load_config
from satyasutra.ingest import IngestStats, author_salt, parse_event, stream_posts

CONFIG = Path(__file__).resolve().parents[2] / "config"
LONG = "The new bridge in the capital collapsed this morning and hundreds were injured, officials say."


def cfg(**over):
    return load_config(CONFIG).runtime.ingest.model_copy(update=over)


def ev(did="did:plc:abc", rkey="r1", text=LONG, created="2026-10-30T20:08:12.899Z", langs=("en",), time_us=1000, op="create",
       collection="app.bsky.feed.post", reply=False, kind="commit"):
    """Shaped like a real Jetstream message (keys taken from a live capture on 2026-10-30)."""
    record = {"$type": collection, "createdAt": created, "text": text}
    if langs is not None:
        record["langs"] = list(langs)
    if reply:
        record["reply"] = {"parent": {}, "root": {}}
    msg = {"did": did, "time_us": time_us, "kind": kind}
    if kind == "commit":
        msg["commit"] = {"rev": "x", "operation": op, "collection": collection, "rkey": rkey, "record": record, "cid": "c"}
    return json.dumps(msg)


def parse(raw, c=None, salt="s"):
    stats = IngestStats()
    return parse_event(raw, c or cfg(), salt, stats), stats


def test_a_real_shaped_post_becomes_a_validated_post():
    post, stats = parse(ev())
    assert post.id == "at://did:plc:abc/app.bsky.feed.post/r1"
    assert post.url == "https://bsky.app/profile/did:plc:abc/post/r1"
    assert post.lang == "en" and post.source_id == "bluesky_jetstream" and post.posted_at.year == 2026
    assert stats.posts == 1 and stats.skipped == {}


def test_author_is_a_salted_hash_never_the_did():
    a, _ = parse(ev(), salt="one")
    b, _ = parse(ev(), salt="two")
    assert "did:plc" not in a.author_hash and a.author_hash != b.author_hash and len(a.author_hash) == 64


@pytest.mark.parametrize(
    "raw,reason",
    [
        (ev(op="delete"), "not_create"),
        (ev(kind="identity"), "not_commit"),
        (ev(collection="app.bsky.feed.like"), "other_collection"),
        (ev(reply=True), "reply"),
        (ev(text="too short"), "too_short"),
        (ev(langs=("fr",)), "language"),
        (ev(langs=None), "language"),
    ],
)
def test_filters_skip_and_count_by_reason(raw, reason):
    post, stats = parse(raw)
    assert post is None and stats.skipped == {reason: 1}


def test_keyword_filter_only_applies_when_configured():
    assert parse(ev(), cfg(keywords=["flood"]))[0] is None
    assert parse(ev(), cfg(keywords=["BRIDGE"]))[0] is not None


def test_empty_language_list_means_every_language():
    assert parse(ev(langs=("fr",)), cfg(langs=[]))[0] is not None


@pytest.mark.parametrize("raw", ["not json", "{}", json.dumps({"kind": "commit", "time_us": 1}), ev(created="not a date"), json.dumps(
    {"did": "d", "time_us": 5, "kind": "commit", "commit": {"operation": "create", "collection": "app.bsky.feed.post", "rkey": "r", "record": {"createdAt": "2026-01-01T00:00:00Z"}}})])
def test_malformed_events_are_counted_and_skipped_not_raised(raw):
    post, stats = parse(raw)
    assert post is None and stats.malformed == 1 and stats.posts == 0


def test_cursor_advances_with_every_event_even_skipped_ones():
    stats = IngestStats()
    parse_event(ev(op="delete", time_us=111), cfg(), "s", stats)
    parse_event(ev(rkey="r2", time_us=222), cfg(), "s", stats)
    assert stats.last_cursor_us == 222 and stats.events == 2


def test_author_salt_uses_env_or_a_random_per_process_value():
    assert author_salt({"FORGE_AUTHOR_SALT": "fixed"}) == "fixed"
    assert author_salt({}) != author_salt({})


def test_stream_reconnects_resumes_from_cursor_and_drops_overlap():
    paths: list[str] = []
    batches = [
        [ev(rkey="r1", time_us=5_000_000), ev(rkey="r2", time_us=6_000_000)],
        [ev(rkey="r2", time_us=6_000_000), ev(rkey="r3", time_us=7_000_000)],
    ]

    async def handler(ws):
        paths.append(ws.request.path)
        for msg in batches[len(paths) - 1]:
            await ws.send(msg)
        await ws.close()

    async def run():
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            c = cfg(ws_url=f"ws://127.0.0.1:{port}/subscribe", reconnect_delay_s=0.01, idle_timeout_s=5, resume_overlap_s=2)
            stats = IngestStats()
            got = []
            async for post in stream_posts(c, stats, "s"):
                got.append(post.id.rsplit("/", 1)[1])
                if len(got) == 3:
                    break
            return got, stats

    got, stats = asyncio.run(asyncio.wait_for(run(), 20))
    assert got == ["r1", "r2", "r3"]
    assert stats.skipped.get("duplicate") == 1 and stats.reconnects >= 1
    assert "cursor=4000000" in paths[1]            # last cursor 6,000,000 minus 2 s overlap
    assert "wantedCollections=app.bsky.feed.post" in paths[0] and "cursor" not in paths[0]


def _drain(c, stats, seconds, connect=None):
    async def consume():
        kw = {"connect": connect} if connect else {}
        async for _ in stream_posts(c, stats, "s", **kw):
            pass

    async def run():
        try:
            await asyncio.wait_for(consume(), seconds)
        except asyncio.TimeoutError:
            pass

    asyncio.run(run())


def test_stream_keeps_retrying_when_the_connection_is_refused():
    attempts = []

    def refuse(url, **kw):
        attempts.append(url)
        raise ConnectionRefusedError

    stats = IngestStats()
    _drain(cfg(reconnect_delay_s=0.01, max_reconnect_delay_s=0.02), stats, 0.3, connect=refuse)
    assert len(attempts) >= 3 and stats.reconnects >= 2 and stats.posts == 0


def test_stream_reconnects_when_the_server_goes_silent():
    connections = []

    async def handler(ws):
        connections.append(1)
        await asyncio.sleep(2)

    async def run(stats):
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            c = cfg(ws_url=f"ws://127.0.0.1:{port}/subscribe", reconnect_delay_s=0.01, idle_timeout_s=0.1)

            async def consume():
                async for _ in stream_posts(c, stats, "s"):
                    pass

            try:
                await asyncio.wait_for(consume(), 1.0)
            except asyncio.TimeoutError:
                pass

    stats = IngestStats()
    asyncio.run(run(stats))
    assert len(connections) >= 2 and stats.reconnects >= 1
