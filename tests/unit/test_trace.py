"""Origin and spread of a cluster: first post, wording changes, timeline, anonymous accounts, honest limits."""

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from satyasutra import __version__
from satyasutra.api.app import create_app
from satyasutra.cache import TTLCache
from satyasutra.config import load_config
from satyasutra.fastclock import FastClock, FastClockWorker
from satyasutra.fastclock.trace import word_diff
from satyasutra.ledger import SQLiteLedgerStore
from satyasutra.schemas import Post

CONFIG = Path(__file__).resolve().parents[2] / "config"
BASE = ("breaking the government has announced that all bank deposits above fifty thousand rupees will be frozen from monday morning "
        "so withdraw your money today and share this message with everyone you know before the banks open tomorrow")
CHANGED = BASE.replace("monday", "tuesday")


def make(tmp_path, **fastclock):
    cfg = load_config(CONFIG)
    fc = cfg.runtime.fastclock.model_copy(update=fastclock)
    cfg = cfg.model_copy(update={"runtime": cfg.runtime.model_copy(update={"fastclock": fc})})
    clock = [1000.0]
    fastclock_obj = FastClock(cfg, TTLCache(tmp_path / "c.sqlite3"), clock=lambda: clock[0])
    worker = FastClockWorker(cfg, fastclock_obj, salt="s", wall=lambda: clock[0])
    return cfg, worker, clock


def post(text, n, author):
    now = datetime(2026, 10, 31, 12, 0, n, tzinfo=timezone.utc)
    return Post(id=f"at://did:plc:{author}/app.bsky.feed.post/r{n}", source_id="s", url="https://x.org", author_hash=author * 16, text=text, posted_at=now, fetched_at=now)


def feed(worker, clock, items):
    for at, text, n, author in items:
        clock[0] = at
        worker.fastclock.process(post(text, n, author))


def test_word_diff_lists_removed_and_added_words_in_order():
    assert word_diff("freeze deposits above fifty thousand", "freeze deposits above twenty five thousand") == [
        {"op": "removed", "text": "fifty"}, {"op": "added", "text": "twenty five"}]
    assert word_diff("same words", "same words") == []


def test_trace_names_the_first_post_counts_accounts_and_shows_the_wording_change(tmp_path):
    cfg, worker, clock = make(tmp_path)
    feed(worker, clock, [(1000, BASE, 1, "a"), (1010, BASE, 2, "b"), (1020, BASE, 3, "a"), (1060, CHANGED, 4, "c")])
    cluster_id = worker.top()[0]["cluster_id"]
    t = worker.trace(cluster_id)
    assert t["first_post"]["text"] == BASE and t["first_post"]["account"] == 1 and "did:plc:a" in t["first_post"]["url"]
    assert t["posts_seen"] == 4 and t["posts_stored"] == 4 and t["stored_all_posts"] and t["distinct_accounts_stored"] == 3
    assert t["span_s"] == 60.0 and t["timeline"][-1] == {"at_s": 60.0, "posts": 4, "accounts": 3}
    assert [v["posts"] for v in t["variants"]] == [3, 1] and t["variants"][1]["diff_from_previous"] == [
        {"op": "removed", "text": "monday"}, {"op": "added", "text": "tuesday"}]
    assert "not necessarily the true origin" in t["scope_note"] and "Bluesky" in t["scope_note"]


def test_account_hashes_never_appear_in_a_trace_and_a_full_cluster_is_reported_as_truncated(tmp_path):
    cfg, worker, clock = make(tmp_path, members_kept=2)
    feed(worker, clock, [(1000 + i, BASE, i, "abcd"[i % 4]) for i in range(4)])
    t = worker.trace(worker.top()[0]["cluster_id"])
    assert t["posts_seen"] == 4 and t["posts_stored"] == 2 and not t["stored_all_posts"]
    assert t["first_post"]["text"] == BASE                                   # the origin is kept, later posts are the ones dropped
    assert "a" * 16 not in repr(t) and "b" * 16 not in repr(t)


def test_a_claim_matches_a_live_cluster_only_when_it_is_spreading_and_unrelated_text_matches_nothing(tmp_path):
    cfg, worker, clock = make(tmp_path)
    feed(worker, clock, [(1000, BASE, 1, "a")])
    assert worker.match(BASE) is None                                         # one post is not a spreading claim
    feed(worker, clock, [(1005, BASE, 2, "b")])
    m = worker.match(BASE)
    assert m and m["posts"] == 2 and m["distinct_accounts"] == 2
    assert worker.match("a completely different sentence about cricket scores in the final over") is None


def test_routes_serve_the_trace_the_match_and_a_clear_404(tmp_path):
    cfg, worker, clock = make(tmp_path)
    feed(worker, clock, [(1000, BASE, 1, "a"), (1005, BASE, 2, "b")])
    client = TestClient(create_app(cfg, SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__), worker=worker))
    cid = client.get("/fastclock/top").json()["clusters"][0]["cluster_id"]
    assert client.get(f"/fastclock/cluster/{cid}").json()["posts_seen"] == 2
    assert client.get("/fastclock/cluster/99999").status_code == 404
    assert client.get("/fastclock/match", params={"text": BASE}).json()["match"]["cluster_id"] == cid
    assert client.get("/fastclock/match", params={"text": "nothing like it at all, just a quiet afternoon"}).json() == {"match": None}
    assert client.get("/fastclock/top").json()["clusters"][0]["distinct_accounts"] == 2
