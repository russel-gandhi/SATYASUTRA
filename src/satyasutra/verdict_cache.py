"""Answer a repeated claim from cache: the same text twice must not call Gemini or the Fact Check API again.

What is cached is the final /verify result, not the model's reply, so a changed rule or threshold is picked up. An entry is a
miss when it was made under a different config checksum or code version, when it has expired, or when the run did not finish
cleanly (failures are never stored). The time-to-live is shorter when nothing was decided, because new fact-checks appear.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .cache import TTLCache
from .config import Config
from .fastclock.normalise import normalise, text_key

KEY_PREFIX = "vc:"
UNDECIDED = "UNVERIFIABLE_YET"
CACHEABLE_STATUSES = ("ok", "no_items")          # an invalid_output, blocked or failed extraction is not an answer


@dataclass
class Cached:
    body: dict
    from_cache: bool
    made_at: float                                # epoch seconds when the verdict was made
    age_s: float


class VerdictCache:
    def __init__(self, cache: TTLCache, config: Config, code_version: str, scope: str = "", clock: Callable[[], float] = time.time):
        self._cache, self._clock = cache, clock
        self._settings = config.runtime.verify
        # `scope` names the ledger the stored ledger_seq values point into: a different ledger file must not reuse them
        self._fingerprint = f"{config.checksum}|{code_version}|{scope}"
        self._guard = threading.Lock()
        self._flights: dict[str, tuple[threading.Lock, int]] = {}

    @staticmethod
    def key(text: str) -> str:
        return KEY_PREFIX + text_key(normalise(text))

    def _ttl(self, body: dict) -> float:
        decided = any(c["label"] != UNDECIDED for c in body.get("claims", []))
        return self._settings.cache_ttl_decided_s if decided else self._settings.cache_ttl_undecided_s

    def _lookup(self, key: str) -> Cached | None:
        hit = self._cache.get(key, max(self._settings.cache_ttl_decided_s, self._settings.cache_ttl_undecided_s))
        if hit is None:
            return None
        entry = json.loads(hit[0])
        age = self._clock() - hit[1]
        if entry["fingerprint"] != self._fingerprint or age > self._ttl(entry["body"]):
            return None
        return Cached(entry["body"], True, hit[1], age)

    def get(self, text: str) -> Cached | None:
        return self._lookup(self.key(text))

    def get_or_compute(self, text: str, compute: Callable[[], dict]) -> Cached:
        """The cached result, or `compute()` run once even when several identical requests arrive together."""
        key = self.key(text)
        hit = self._lookup(key)
        if hit is not None:
            return hit
        with self._guard:
            lock, waiting = self._flights.get(key, (threading.Lock(), 0))
            self._flights[key] = (lock, waiting + 1)
        try:
            with lock:
                hit = self._lookup(key)            # another request may have finished while this one waited
                if hit is not None:
                    return hit
                body = compute()
                if body.get("extraction", {}).get("status") in CACHEABLE_STATUSES:
                    made = self._cache.put(key, json.dumps({"fingerprint": self._fingerprint, "body": body}, sort_keys=True))
                else:
                    made = self._clock()
                return Cached(body, False, made, 0.0)
        finally:
            with self._guard:
                lock, waiting = self._flights[key]
                if waiting <= 1:
                    del self._flights[key]
                else:
                    self._flights[key] = (lock, waiting - 1)


def mark(cached: Cached, now: Callable[[], float] = time.time) -> dict:
    """The response body with cache facts attached: whether it came from cache, when it was made and how old its evidence is."""
    fetched = [datetime.fromisoformat(e["fetched_at"]).timestamp() for c in cached.body.get("claims", []) for e in c.get("citations", [])]
    return {
        **cached.body,
        "from_cache": cached.from_cache,
        "made_at": datetime.fromtimestamp(cached.made_at, timezone.utc).isoformat(),
        "age_s": round(cached.age_s, 3),
        "evidence_age_s": round(now() - min(fetched), 3) if fetched else None,
    }
