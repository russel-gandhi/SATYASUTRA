"""The escalator: only spreading clusters reach the slow path, once each, inside the Gemini budget (F2-05)."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from satyasutra.cache import TTLCache
from satyasutra.config import ThresholdEntry, load_config
from satyasutra.fastclock import Escalator, FastClock, FastClockWorker
from satyasutra.schemas import Post

CONFIG = Path(__file__).resolve().parents[2] / "config"
FLOOR = 0.6                      # 3 distinct authors in the 300 s window
RESERVE = 5                      # of 15 requests a minute, so the budget is 10
RETRY_S = 300


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def text(n: int) -> str:
    """Posts that are not near-duplicates of each other, so each n is its own cluster."""
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]
    return " ".join(f"{words[(n + i) % len(words)]}{n * 7 + i}" for i in range(12))


class World:
    def __init__(self, tmp_path, audit=None):
        base = load_config(CONFIG)
        thresholds = base.thresholds.model_copy(update={"thresholds": base.thresholds.thresholds | {"escalation_min_authors_per_min": ThresholdEntry(value=FLOOR)}})
        esc = base.runtime.escalation.model_copy(update={"reserve_per_minute": RESERVE, "retry_after_s": RETRY_S, "enabled": True})
        self.cfg = base.model_copy(update={"thresholds": thresholds, "runtime": base.runtime.model_copy(update={"escalation": esc})})
        self.clock = Clock()
        self.fc = FastClock(self.cfg, TTLCache(tmp_path / "c.sqlite3", clock=self.clock), clock=self.clock)
        self.worker = FastClockWorker(self.cfg, self.fc, salt="s", wall=self.clock)
        self.calls: list[str] = []
        self.audit = audit or self.ok
        self.esc = Escalator(self.cfg, self.worker, lambda post: self.audit(post), self.make_post, wall=self.clock)

    def ok(self, post):
        self.calls.append(post.id)
        return {"claims": [{"label": "UNVERIFIABLE_YET"}]}

    def make_post(self, cluster):
        now = datetime.now(timezone.utc)
        return Post(id=cluster.rep_post_id, source_id="s", url="", author_hash="h", text=cluster.rep_text, posted_at=now, fetched_at=now)

    def spread(self, n: int, authors: int, prefix: str = "p") -> None:
        """`authors` different accounts post message n, each once."""
        for a in range(authors):
            self.fc.clusterer.add(f"{prefix}{n}-{a}", text(n), self.clock(), author=f"{prefix}{n}-author{a}")
            self.worker.processed += 1

    def run_once(self):
        return asyncio.run(self.esc.run_once())

    def drain(self) -> int:
        n = 0
        while self.run_once() is not None:
            n += 1
        return n


def test_refuses_to_start_without_a_calibrated_floor(tmp_path):
    cfg = load_config(CONFIG)
    bare = cfg.model_copy(update={"thresholds": cfg.thresholds.model_copy(update={"thresholds": {k: v for k, v in cfg.thresholds.thresholds.items() if k != "escalation_min_authors_per_min"}})})
    fc = FastClock(bare, TTLCache(tmp_path / "c.sqlite3"))
    with pytest.raises(ValueError, match="escalation_min_authors_per_min"):
        Escalator(bare, FastClockWorker(bare, fc, salt="s"), lambda p: {}, lambda c: None)


def test_clusters_below_the_floor_are_not_escalated_and_the_budget_is_not_filled(tmp_path):
    w = World(tmp_path)
    w.spread(1, authors=2)                                  # 0.4 authors/min, under the 0.6 floor
    w.spread(2, authors=1)
    assert w.drain() == 0 and w.calls == []
    assert w.esc.stats()["eligible_now"] == 0 and w.esc.stats()["escalations_started"] == 0


def test_a_cluster_at_the_floor_is_audited_exactly_once(tmp_path):
    w = World(tmp_path)
    w.spread(1, authors=3)
    assert w.drain() == 1 and len(w.calls) == 1
    w.spread(1, authors=2, prefix="later")                  # the same cluster keeps growing
    assert w.drain() == 0 and len(w.calls) == 1
    audit = w.esc.audits[next(iter(w.esc.audits))]
    assert audit.status == "done" and audit.members == 3 and audit.authors_per_minute == 0.6 and audit.labels == ["UNVERIFIABLE_YET"]


def test_the_earliest_member_is_the_representative_text(tmp_path):
    w = World(tmp_path)
    w.spread(1, authors=3)
    w.drain()
    assert w.calls == ["p1-0"]


def test_fastest_spreading_cluster_goes_first(tmp_path):
    w = World(tmp_path)
    w.spread(1, authors=3, prefix="slow")
    w.spread(2, authors=6, prefix="fast")
    w.spread(3, authors=4, prefix="mid")
    w.drain()
    assert w.calls == ["fast2-0", "mid3-0", "slow1-0"]


def test_budget_per_minute_is_respected_and_resets_as_time_passes(tmp_path):
    w = World(tmp_path)
    budget = w.esc.budget
    assert budget == 15 - RESERVE
    for n in range(1, 16):
        w.spread(n, authors=3)
    assert w.drain() == budget and len(w.calls) == budget          # the rest wait
    assert w.esc.stats()["escalated_last_minute"] == budget and w.esc.stats()["eligible_now"] == 15 - budget
    w.clock.now += 30
    assert w.run_once() is None                                    # still inside the same minute
    w.clock.now += 31
    assert w.drain() == 15 - budget                                # a minute later the queue moves on
    assert len(w.calls) == 15 and len(set(w.calls)) == 15


def test_user_requests_keep_their_reserve(tmp_path):
    w = World(tmp_path)
    for n in range(1, 31):
        w.spread(n, authors=3)
    w.drain()
    assert len(w.calls) + RESERVE <= w.cfg.policy.gemini.requests_per_minute


def test_a_failed_audit_is_counted_and_waits_before_it_is_tried_again(tmp_path):
    outcomes = iter([RuntimeError("secret-key-123 in a url"), None])

    def flaky(post):
        failure = next(outcomes)
        if failure:
            raise failure
        return {"claims": []}

    w = World(tmp_path, audit=flaky)
    w.spread(1, authors=3)
    first = w.run_once()
    assert first.status == "failed" and w.esc.failed == 1 and w.esc.started == 1
    assert first.error == "RuntimeError" and "secret" not in repr(w.esc.recent(5))      # the class only, never the message
    assert w.run_once() is None                                    # waiting, not looping
    w.clock.now += RETRY_S - 1
    assert w.run_once() is None
    w.clock.now += 2
    w.spread(1, authors=3, prefix="again")                       # still spreading, or the floor would rule it out
    second = w.run_once()
    assert second.status == "done" and w.esc.started == 2 and w.esc.failed == 1
    assert w.run_once() is None                                    # done now, never again


def test_escalation_share_is_audits_over_posts_processed(tmp_path):
    w = World(tmp_path)
    w.spread(1, authors=3)
    for i in range(97):
        w.worker.processed += 1
    w.drain()
    assert w.worker.processed == 100
    assert w.esc.stats()["escalation_share"] == 0.01
    assert w.worker.stats()["escalation_share"] == 0.01            # the same counters appear in /fastclock/stats


def test_share_is_none_before_any_post(tmp_path):
    assert World(tmp_path).esc.stats()["escalation_share"] is None


def test_old_audits_are_forgotten_beyond_the_configured_limit(tmp_path):
    w = World(tmp_path)
    w.esc.settings = w.esc.settings.model_copy(update={"max_audits_kept": 3})
    for n in range(1, 8):
        w.spread(n, authors=3)
        w.drain()
        w.clock.now += 61
    assert len(w.esc.audits) == 3 and w.esc.started == 7


def test_the_background_loop_audits_and_stops_cleanly(tmp_path):
    w = World(tmp_path)
    w.spread(1, authors=3)
    w.esc.settings = w.esc.settings.model_copy(update={"tick_s": 0.01})

    async def go():
        w.esc.start()
        for _ in range(200):
            if w.calls:
                break
            await asyncio.sleep(0.01)
        await w.esc.stop()
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    assert asyncio.run(go()) == [] and len(w.calls) == 1
