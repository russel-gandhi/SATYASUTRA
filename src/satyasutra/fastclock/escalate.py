"""Send only the busiest clusters to the slow path (Gemini and the Fact Check API), one audit per cluster, inside a budget.

About 665 posts a minute arrive and Gemini allows 15 requests a minute, so the fast clock ranks and this module spends the
budget on the clusters that are spreading. A quiet window escalates nothing: the floor is a calibrated threshold, and the
budget is never filled with weak clusters to look busy. The audit itself is a function the caller supplies (the same path
POST /verify uses), so a cluster's result lands in the verdict cache and the ledger like any other.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Callable

from ..config import Config
from ..schemas import Post
from .cluster import Cluster
from .worker import FastClockWorker

SECONDS_PER_MINUTE = 60
THRESHOLD = "escalation_min_authors_per_min"


@dataclass
class Audit:
    cluster_id: int
    post_id: str
    status: str                      # "done" or "failed"
    started: float                   # epoch seconds
    members: int                     # cluster size when it was escalated
    authors_per_minute: float
    labels: list[str] = field(default_factory=list)
    error: str = ""                  # exception class only, never a message


class Escalator:
    def __init__(
        self,
        config: Config,
        worker: FastClockWorker,
        audit: Callable[[Post], dict],
        make_post: Callable[[Cluster], Post],
        wall: Callable[[], float] = time.time,
    ):
        entry = config.thresholds.thresholds.get(THRESHOLD)
        if entry is None:
            raise ValueError(f"config/thresholds.yaml has no calibrated {THRESHOLD}; run scripts/calibrate_escalation.py")
        self.floor = entry.value
        self.settings = config.runtime.escalation
        self.window = config.runtime.fastclock.window_s
        self.budget = config.policy.gemini.requests_per_minute - self.settings.reserve_per_minute
        if self.budget <= 0:
            raise ValueError("escalation.reserve_per_minute leaves no Gemini budget for escalation")
        self.worker, self._audit, self._make_post, self._wall = worker, audit, make_post, wall
        self.audits: OrderedDict[int, Audit] = OrderedDict()   # for reporting only; forgetting one must not cause a repeat audit
        self._done: set[int] = set()                 # clusters audited, kept while the cluster lives
        self._retry_at: dict[int, float] = {}        # failed clusters and when they may be tried again
        self._spent: deque[float] = deque()          # start times of audits inside the last minute
        self.started = self.failed = 0
        self._task: asyncio.Task | None = None
        worker.escalator = self                      # so /fastclock/stats can report the escalation counters

    # decisions -----------------------------------------------------------------------------------------------------------

    def _spent_in_last_minute(self, now: float) -> int:
        while self._spent and self._spent[0] < now - SECONDS_PER_MINUTE:
            self._spent.popleft()
        return len(self._spent)

    def eligible(self, now: float) -> list[tuple[Cluster, float]]:
        """Clusters at or above the floor that have not been audited (or whose failure wait is over), fastest spread first."""
        out = []
        clusterer = self.worker.fastclock.clusterer
        self._done &= clusterer.clusters.keys()      # an evicted cluster can never come back, so its marks go too
        self._retry_at = {k: v for k, v in self._retry_at.items() if k in clusterer.clusters}
        for cluster, _ in clusterer.top(now, len(clusterer.clusters) or None):
            rate = cluster.author_velocity(now, self.window)
            if rate < self.floor or cluster.id in self._done or now < self._retry_at.get(cluster.id, now):
                continue
            out.append((cluster, rate))
        return out

    def next_cluster(self, now: float) -> tuple[Cluster, float] | None:
        if self._spent_in_last_minute(now) >= self.budget:
            return None
        ranked = self.eligible(now)
        return ranked[0] if ranked else None

    # one audit -----------------------------------------------------------------------------------------------------------

    def _record(self, audit: Audit) -> None:
        self.audits[audit.cluster_id] = audit
        self.audits.move_to_end(audit.cluster_id)
        while len(self.audits) > self.settings.max_audits_kept:
            self.audits.popitem(last=False)

    async def run_once(self) -> Audit | None:
        """Audit the best eligible cluster if the budget allows. Returns the audit, or None when there was nothing to do."""
        now = self._wall()
        chosen = self.next_cluster(now)
        if chosen is None:
            return None
        cluster, rate = chosen
        self._spent.append(now)
        self.started += 1
        audit = Audit(cluster.id, cluster.rep_post_id, "done", now, cluster.size, round(rate, 3))
        try:
            body = await asyncio.to_thread(self._audit, self._make_post(cluster))
            audit.labels = [c["label"] for c in body.get("claims", [])]
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                   # a failed audit is counted and retried later, never looped on
            audit.status, audit.error = "failed", type(exc).__name__
            self._retry_at[cluster.id] = self._wall() + self.settings.retry_after_s
            self.failed += 1
        else:
            self._done.add(cluster.id)
            self._retry_at.pop(cluster.id, None)
        self._record(audit)
        return audit

    # lifecycle and reporting ---------------------------------------------------------------------------------------------

    async def _loop(self) -> None:
        while True:
            if await self.run_once() is None:
                await asyncio.sleep(self.settings.tick_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="fastclock-escalate")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def stats(self) -> dict:
        now = self._wall()
        processed = self.worker.processed
        return {
            "escalation_floor_authors_per_minute": self.floor,
            "escalation_budget_per_minute": self.budget,
            "escalations_started": self.started,
            "escalations_failed": self.failed,
            "escalated_last_minute": self._spent_in_last_minute(now),
            "eligible_now": len(self.eligible(now)),
            "escalation_share": round(self.started / processed, 5) if processed else None,
        }

    def recent(self, n: int) -> list[dict]:
        return [
            {"cluster_id": a.cluster_id, "post_id": a.post_id, "status": a.status, "labels": a.labels, "error": a.error,
             "members_when_escalated": a.members, "authors_per_minute": a.authors_per_minute,
             "started": a.started}
            for a in list(self.audits.values())[-n:][::-1]
        ]
