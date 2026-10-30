"""Google Fact Check Tools adapter: a claim in, the fact-checkers' own reviews out, as Evidence.

It does not decide anything. The publisher's rating text is kept untouched; mapping a rating to a label is the verdict
rules' job (F1-07). A review is about a claim that may only be similar to ours, so the role is "context", not "supports".
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Callable, Mapping

import httpx

from ..cache import TTLCache
from ..config import Config
from ..errors import ConfigError, QuotaExhausted, RateLimited, SchemaChanged, Unreachable
from ..schemas import Evidence
from ..ratelimit import Spacer
from ..schemas.core import sha256_text
from .probe import build_user_agent

SOURCE_ID = "factcheck_tools"
SECONDS_PER_MINUTE = 60


def normalise_query(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass
class FactCheckResult:
    evidence: list[Evidence]
    skipped_reviews: int          # reviews missing a url or rating, which cannot be cited
    from_cache: bool


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def passage_for(publisher: str, rating: str, reviewed_claim: str, title: str) -> str:
    return f'{publisher or "A fact-checker"} rated the claim "{reviewed_claim}" as "{rating}". {title}'.strip()


def map_response(body: object, tier: int, fetched_at: datetime) -> tuple[list[Evidence], int]:
    """Raw API JSON to Evidence. An empty object is a normal 'nothing found'; any other shape change raises."""
    if not isinstance(body, dict):
        raise SchemaChanged(SOURCE_ID, "response is not a JSON object")
    claims = body.get("claims", [])
    if not isinstance(claims, list):
        raise SchemaChanged(SOURCE_ID, "'claims' is not a list")
    evidence: list[Evidence] = []
    skipped = 0
    for claim in claims:
        if not isinstance(claim, dict):
            raise SchemaChanged(SOURCE_ID, "a claim entry is not an object")
        reviews = claim.get("claimReview", [])
        if not isinstance(reviews, list):
            raise SchemaChanged(SOURCE_ID, "'claimReview' is not a list")
        for review in reviews:
            url, rating = (review or {}).get("url"), (review or {}).get("textualRating")
            if not (isinstance(url, str) and url and isinstance(rating, str) and rating):
                skipped += 1
                continue
            publisher = str((review.get("publisher") or {}).get("name", ""))
            reviewed_claim = str(claim.get("text", ""))
            passage = passage_for(publisher, rating, reviewed_claim, str(review.get("title", "")))
            evidence.append(Evidence(
                role="context", route="factcheck", source_id=SOURCE_ID, url=url, fetched_at=fetched_at, tier=tier,
                passage=passage, passage_sha256=sha256_text(passage),
                publisher=publisher, rating=rating, reviewed_claim=reviewed_claim, reviewed_at=_parse_time(review.get("reviewDate")),
            ))
    return evidence, skipped


class FactCheckClient:
    def __init__(
        self,
        config: Config,
        cache: TTLCache,
        client: httpx.Client | None = None,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._src = config.source(SOURCE_ID)
        self._settings = config.runtime.factcheck
        self._cache, self._env, self._clock = cache, env, clock
        self._http = client or httpx.Client(timeout=self._settings.timeout_s, headers={"User-Agent": build_user_agent(config.runtime.probe.user_agent)})
        self._spacer = Spacer(self._src.rate_limit_per_min, clock, sleep)

    def _key(self) -> str:
        env = os.environ if self._env is None else self._env
        key = env.get(self._src.auth_env or "", "")
        if not key:
            raise ConfigError(f"set {self._src.auth_env} to use {SOURCE_ID}")
        return key

    def search(self, text: str, language: str | None = None) -> FactCheckResult:
        query = normalise_query(text)
        if not query:
            return FactCheckResult([], 0, False)
        cache_key = hashlib.sha256(f"{query}|{language or ''}|{self._settings.page_size}".encode("utf-8")).hexdigest()

        hit = self._cache.get(cache_key, self._src.cache_ttl_s)
        if hit is not None:
            payload, fetched = hit
            ev, skipped = map_response(json.loads(payload), self._src.tier, datetime.fromtimestamp(fetched, timezone.utc))
            return FactCheckResult(ev, skipped, True)

        params = {"query": query, "pageSize": self._settings.page_size, self._src.auth_param or "key": self._key()}
        if language:
            params["languageCode"] = language
        self._spacer.wait()
        try:
            response = self._http.get(self._settings.endpoint, params=params)
        except httpx.TimeoutException:
            raise Unreachable(SOURCE_ID, "timed out") from None
        except httpx.HTTPError as exc:
            raise Unreachable(SOURCE_ID, type(exc).__name__) from None      # class name only: a message may echo the key

        code = response.status_code
        if code == HTTPStatus.TOO_MANY_REQUESTS:
            raise RateLimited(SOURCE_ID, "HTTP 429")
        if code == HTTPStatus.FORBIDDEN:
            raise QuotaExhausted(SOURCE_ID, "HTTP 403: key refused or quota used up")
        if code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            raise Unreachable(SOURCE_ID, f"HTTP {code}")
        if code != HTTPStatus.OK:
            raise SchemaChanged(SOURCE_ID, f"HTTP {code}: request not accepted (bad key or changed API)")
        try:
            body = response.json()
        except ValueError:
            raise SchemaChanged(SOURCE_ID, "response is not JSON") from None

        fetched = self._clock()
        evidence, skipped = map_response(body, self._src.tier, datetime.fromtimestamp(fetched, timezone.utc))
        self._cache.put(cache_key, json.dumps(body), at=fetched)      # only answers that parsed are cached; same timestamp as the evidence
        return FactCheckResult(evidence, skipped, False)
