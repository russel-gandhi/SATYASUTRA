"""Group near-duplicate posts. A post joins the existing cluster whose first post it resembles most (estimated Jaccard at or
above the calibrated threshold); otherwise it starts a new cluster. Candidates come from LSH buckets, so cost does not grow
with the number of clusters."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..config import FastClockSettings
from .minhash import MinHasher, estimate_jaccard
from .normalise import normalise, shingles

SECONDS_PER_MINUTE = 60


@dataclass
class Cluster:
    id: int
    signature: tuple[int, ...]
    rep_post_id: str
    rep_text: str
    first_seen: float
    last_seen: float
    size: int = 1
    stamps: deque = field(default_factory=deque, repr=False)     # (arrival time, author) inside the velocity window

    def _trim(self, now: float, window_s: float) -> None:
        while self.stamps and self.stamps[0][0] < now - window_s:
            self.stamps.popleft()

    def velocity(self, now: float, window_s: float) -> float:
        """Posts per minute over the last window_s seconds."""
        self._trim(now, window_s)
        return len(self.stamps) / (window_s / SECONDS_PER_MINUTE)

    def author_velocity(self, now: float, window_s: float) -> float:
        """Distinct authors per minute. One account repeating a template does not look like a spreading claim."""
        self._trim(now, window_s)
        return len({author for _, author in self.stamps}) / (window_s / SECONDS_PER_MINUTE)


class Clusterer:
    def __init__(self, settings: FastClockSettings, threshold: float):
        self.s, self.threshold = settings, threshold
        self.hasher = MinHasher(settings.num_perm)
        self.rows = settings.num_perm // settings.lsh_bands
        self.clusters: dict[int, Cluster] = {}
        self._buckets: dict[tuple[int, tuple[int, ...]], set[int]] = {}
        self._next_id = 1
        self.dropped_total = 0                 # clusters removed so far, idle or to make room

    def _band_keys(self, sig: tuple[int, ...]):
        for b in range(self.s.lsh_bands):
            yield b, sig[b * self.rows:(b + 1) * self.rows]

    def add(self, post_id: str, text: str, now: float, author: str = "") -> tuple[Cluster, bool] | None:
        """Place one post. Returns (cluster, created) or None when the text has nothing left after normalising."""
        norm = normalise(text)
        grams = shingles(norm, self.s.shingle_words)
        if not grams:
            return None
        self.evict(now)
        sig = self.hasher.signature(grams)

        candidates = {cid for key in self._band_keys(sig) for cid in self._buckets.get(key, ())}
        best, best_sim = None, 0.0
        for cid in candidates:
            sim = estimate_jaccard(sig, self.clusters[cid].signature)
            if sim >= self.threshold and sim > best_sim:
                best, best_sim = self.clusters[cid], sim
        if best is not None:
            best.size += 1
            best.last_seen = now
            best.stamps.append((now, author))
            return best, False

        if len(self.clusters) >= self.s.max_clusters:
            self._drop(min(self.clusters.values(), key=lambda c: c.last_seen))
        cluster = Cluster(self._next_id, sig, post_id, text, now, now, stamps=deque([(now, author)]))
        self._next_id += 1
        self.clusters[cluster.id] = cluster
        for key in self._band_keys(sig):
            self._buckets.setdefault(key, set()).add(cluster.id)
        return cluster, True

    def _drop(self, cluster: Cluster) -> None:
        self.clusters.pop(cluster.id, None)
        self.dropped_total += 1
        for key in self._band_keys(cluster.signature):
            bucket = self._buckets.get(key)
            if bucket:
                bucket.discard(cluster.id)
                if not bucket:
                    del self._buckets[key]

    def evict(self, now: float) -> int:
        stale = [c for c in self.clusters.values() if now - c.last_seen > self.s.cluster_idle_s]
        for c in stale:
            self._drop(c)
        return len(stale)

    def top(self, now: float, n: int | None = None) -> list[tuple[Cluster, float]]:
        """Clusters by distinct authors per minute, then posts per minute, size and recency. The number returned is posts per minute."""
        n = n or self.s.top_n
        w = self.s.window_s
        scored = sorted(self.clusters.values(), key=lambda c: (c.author_velocity(now, w), c.velocity(now, w), c.size, c.last_seen), reverse=True)
        return [(c, c.velocity(now, w)) for c in scored[:n]]
