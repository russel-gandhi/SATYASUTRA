"""The no-model fast clock: normalise, cluster, and answer repeats from a cache. Anything that needs a model is the caller's
`resolve` function, and it runs only for text never resolved before (within the cache TTL)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

from ..cache import TTLCache
from ..config import Config
from ..schemas import Post
from .cluster import Cluster, Clusterer
from .normalise import normalise, text_key


@dataclass
class Outcome:
    cluster: Cluster | None        # None when the post had no text left after normalising
    created_cluster: bool
    result: dict | None            # what resolve returned, or the cached copy; None when no resolver was given
    cached: bool


class FastClock:
    def __init__(self, config: Config, cache: TTLCache, clock: Callable[[], float] = time.time):
        entry = config.thresholds.thresholds.get("cluster_jaccard")
        if entry is None:
            raise ValueError("config/thresholds.yaml has no calibrated cluster_jaccard; run scripts/calibrate_fastclock.py")
        self.settings = config.runtime.fastclock
        self.clusterer = Clusterer(self.settings, entry.value)
        self._cache, self._clock = cache, clock

    def process(self, post: Post, resolve: Callable[[Post], dict] | None = None) -> Outcome:
        now = self._clock()
        placed = self.clusterer.add(post.id, post.text, now, post.author_hash)     # every post counts toward velocity, cached or not
        if placed is None:
            return Outcome(None, False, None, False)
        cluster, created = placed
        if resolve is None:
            return Outcome(cluster, created, None, False)

        key = "fc:" + text_key(normalise(post.text))
        hit = self._cache.get(key, self.settings.result_ttl_s)
        if hit is not None:
            return Outcome(cluster, created, json.loads(hit[0]), True)
        result = resolve(post)
        self._cache.put(key, json.dumps(result, sort_keys=True))
        return Outcome(cluster, created, result, False)

    def top(self, n: int | None = None) -> list[tuple[Cluster, float]]:
        return self.clusterer.top(self._clock(), n)
