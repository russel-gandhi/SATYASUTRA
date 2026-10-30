"""Reachability probe for every configured source (plan Q3).

It answers one question honestly: from this machine, right now, can we reach each source, and if not, why.
It never returns an empty result for a failure and never puts a key in a message.
"""

from __future__ import annotations

import os
import time
from http import HTTPStatus
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal, Mapping

import httpx

from ..config import Config, SourceConfig
from ..errors import RateLimited, SourceError, Unreachable

ProbeState = Literal["reachable", "unreachable", "needs_key", "rate_limited", "blocked"]


@dataclass(frozen=True)
class ProbeResult:
    source_id: str
    role: str
    tier: int
    state: ProbeState
    http_status: int | None
    latency_ms: int | None
    detail: str
    checked_at: str

    def as_dict(self) -> dict:
        return asdict(self)

    def as_error(self) -> SourceError | None:
        """The typed error a caller would raise for this result, or None when the source is usable."""
        if self.state == "unreachable":
            return Unreachable(self.source_id, self.detail)
        if self.state == "rate_limited":
            return RateLimited(self.source_id, self.detail)
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def probe_source(src: SourceConfig, client: httpx.Client, env: Mapping[str, str] | None = None) -> ProbeResult:
    env = os.environ if env is None else env

    def result(state: ProbeState, status=None, latency=None, detail="") -> ProbeResult:
        return ProbeResult(src.id, src.role, src.tier, state, status, latency, detail, _now())

    url = httpx.URL(src.probe_url)
    if src.auth_env and src.auth_param:
        key = env.get(src.auth_env)
        if not key:
            return result("needs_key", detail=f"set {src.auth_env} to probe this source")
        url = url.copy_merge_params({src.auth_param: key})  # keep the URL's own query, add the key

    started = time.perf_counter()
    try:
        response = client.get(url)
    except httpx.TimeoutException:
        return result("unreachable", detail="timed out")
    except httpx.HTTPError as exc:
        return result("unreachable", detail=type(exc).__name__)       # class name only: a message may echo the URL and key
    latency = int((time.perf_counter() - started) * 1000)
    code = response.status_code

    if code == HTTPStatus.TOO_MANY_REQUESTS:
        return result("rate_limited", code, latency, "HTTP 429")
    if code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        return result("blocked", code, latency, f"HTTP {code}: reachable, but access refused")
    if code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return result("unreachable", code, latency, f"HTTP {code}")
    if code >= HTTPStatus.BAD_REQUEST:
        return result("unreachable", code, latency, f"HTTP {code}: unexpected for a probe URL")
    return result("reachable", code, latency, "ok")


def probe_all(config: Config, client: httpx.Client | None = None, env: Mapping[str, str] | None = None) -> list[ProbeResult]:
    own = client is None
    http = client or httpx.Client(
        timeout=config.runtime.probe.timeout_s,
        headers={"User-Agent": config.runtime.probe.user_agent},
        follow_redirects=True,
    )
    try:
        sources = [s for s in config.sources.sources if s.status != "removed"]
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(sources)))) as pool:
            return list(pool.map(lambda s: probe_source(s, http, env), sources))
    finally:
        if own:
            http.close()
