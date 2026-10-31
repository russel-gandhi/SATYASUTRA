"""The fast clock as a long-running task: live posts in, ranked clusters and counters out. No model is called here.

Two tasks share one event loop: a producer reads the Jetstream stream into a bounded queue, a consumer feeds the fast clock.
Everything stays on the loop because the clusterer is not thread-safe. If processing falls behind the stream, the OLDEST queued
posts are dropped and counted: velocity cares about what is spreading now, and the stats say plainly that posts were skipped.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import AsyncIterator, Callable

from ..config import Config
from ..ingest import IngestStats, author_salt, stream_posts
from ..schemas import Post
from .clock import FastClock
from .normalise import normalise, text_key
from .trace import build_trace

SECONDS_PER_MINUTE = 60
AT_URI_PARTS = 3                       # did, collection, rkey
MS = 1000
MEDIAN, P95 = 0.5, 0.95


def percentile(sorted_values: list[float], q: float) -> float | None:
    """Nearest-rank percentile of an already sorted list."""
    if not sorted_values:
        return None
    return sorted_values[max(0, min(len(sorted_values) - 1, int(q * len(sorted_values) + 0.999999) - 1))]


class FastClockWorker:
    def __init__(
        self,
        config: Config,
        fastclock: FastClock,
        salt: str | None = None,
        process: Callable[[Post], object] | None = None,
        stream: Callable[..., AsyncIterator[Post]] = stream_posts,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ):
        self.config, self.fastclock = config, fastclock
        self.settings = config.runtime.fastclock
        self._process = process or fastclock.process
        self._stream, self._salt = stream, salt or author_salt()
        self._mono, self._wall = monotonic, wall
        self.ingest = IngestStats()
        self._queue: deque[Post] = deque()
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._repeat_keys: OrderedDict[str, None] = OrderedDict()
        self._times_ms: deque[float] = deque(maxlen=self.settings.latency_samples)
        self._processed_at: deque[float] = deque()
        self.posts_in = self.processed = self.dropped = self.exact_repeats = 0
        self.started_mono: float | None = None
        self.last_post_mono: float | None = None
        self.error = ""
        self.escalator = None                      # set by Escalator so its counters appear in stats()

    # lifecycle ---------------------------------------------------------------------------------------------------------

    def start(self) -> None:
        if self._tasks:
            return
        self.started_mono = self._mono()
        self._tasks = [asyncio.create_task(self._produce(), name="fastclock-produce"), asyncio.create_task(self._consume(), name="fastclock-consume")]

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @property
    def running(self) -> bool:
        return bool(self._tasks) and not any(t.done() for t in self._tasks)

    # the two tasks -----------------------------------------------------------------------------------------------------

    async def _produce(self) -> None:
        try:
            async for post in self._stream(self.config.runtime.ingest, self.ingest, self._salt):
                self.posts_in += 1
                self.last_post_mono = self._mono()
                if len(self._queue) >= self.settings.queue_max:
                    self._queue.popleft()
                    self.dropped += 1
                self._queue.append(post)
                self._wake.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:                      # the stream already reconnects; anything else is a bug worth showing
            self.error = type(exc).__name__

    async def _consume(self) -> None:
        while True:
            await self._wake.wait()
            while self._queue:
                post = self._queue.popleft()
                key = text_key(normalise(post.text))
                if key in self._repeat_keys:
                    self.exact_repeats += 1
                    self._repeat_keys.move_to_end(key)
                else:
                    self._repeat_keys[key] = None
                    if len(self._repeat_keys) > self.settings.repeat_keys_kept:
                        self._repeat_keys.popitem(last=False)
                began = time.perf_counter()
                self._process(post)
                self._times_ms.append((time.perf_counter() - began) * MS)
                self.processed += 1
                self._processed_at.append(self._wall())
                await asyncio.sleep(0)                # let the stream and the HTTP endpoints run between posts
            self._wake.clear()

    # what the endpoints serve --------------------------------------------------------------------------------------------

    def _posts_per_minute(self) -> float:
        now, window = self._wall(), self.settings.window_s
        while self._processed_at and self._processed_at[0] < now - window:
            self._processed_at.popleft()
        if self.started_mono is None:
            return 0.0
        span = min(window, self._mono() - self.started_mono)
        return len(self._processed_at) / span * SECONDS_PER_MINUTE if span > 0 else 0.0

    def stats(self) -> dict:
        times = sorted(self._times_ms)
        clusterer = self.fastclock.clusterer
        out = {
            "running": self.running,
            "error": self.error,
            "uptime_s": round(self._mono() - self.started_mono, 1) if self.started_mono is not None else 0.0,
            "posts_received": self.posts_in,
            "posts_processed": self.processed,
            "posts_per_minute": round(self._posts_per_minute(), 1),
            "posts_dropped": self.dropped,
            "queue_length": len(self._queue),
            "exact_repeats": self.exact_repeats,
            "clusters_now": len(clusterer.clusters),
            "clusters_dropped": clusterer.dropped_total,
            "process_ms_median": round(percentile(times, MEDIAN), 3) if times else None,
            "process_ms_p95": round(percentile(times, P95), 3) if times else None,
            "stream_reconnects": self.ingest.reconnects,
            "stream_events": self.ingest.events,
            "stream_skipped": dict(self.ingest.skipped),
            "last_post_age_s": round(self._mono() - self.last_post_mono, 1) if self.last_post_mono is not None else None,
        }
        if self.escalator is not None:
            out |= self.escalator.stats()
        return out

    def post_url(self, post_id: str) -> str | None:
        """The public web address of a post from its at:// id, or None for ids that are not Bluesky posts."""
        prefix = self.config.runtime.verify.uri_prefix
        parts = post_id.removeprefix(prefix).split("/")
        if not post_id.startswith(prefix) or len(parts) != AT_URI_PARTS:
            return None
        return self.config.runtime.ingest.post_url_template.format(did=parts[0], rkey=parts[2])

    def trace(self, cluster_id: int) -> dict | None:
        cluster = self.fastclock.clusterer.clusters.get(cluster_id)
        return build_trace(cluster, self._wall(), self.settings, self.post_url) if cluster else None

    def match(self, text: str) -> dict | None:
        """The live cluster a pasted claim belongs to, if any: a claim is only 'spreading' when near-identical posts are on the stream."""
        cluster = self.fastclock.clusterer.find(text)
        if cluster is None or cluster.size < self.settings.match_min_posts:
            return None
        return {"cluster_id": cluster.id, "posts": cluster.size, "distinct_accounts": len(cluster.accounts),
                "authors_per_minute": round(cluster.author_velocity(self._wall(), self.settings.window_s), 2)}

    def top(self, n: int | None = None) -> list[dict]:
        """Ranked clusters. Author hashes are never included (Bible D27); the example is the cluster's earliest post."""
        now, window, limit = self._wall(), self.settings.window_s, self.settings.example_text_chars
        out = []
        for cluster, per_minute in self.fastclock.top(n):
            out.append({
                "cluster_id": cluster.id,
                "size": cluster.size,
                "distinct_accounts": len(cluster.accounts),
                "authors_per_minute": round(cluster.author_velocity(now, window), 2),
                "posts_per_minute": round(per_minute, 2),
                "first_seen": datetime.fromtimestamp(cluster.first_seen, timezone.utc).isoformat(),
                "last_seen": datetime.fromtimestamp(cluster.last_seen, timezone.utc).isoformat(),
                "example_url": self.post_url(cluster.rep_post_id),
                "example_text": cluster.rep_text[:limit],
            })
        return out
