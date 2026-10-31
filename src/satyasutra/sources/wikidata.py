"""Wikidata entity lookup: a name in, the best-matching entity (id, label, description, page) out.

This grounds the names in a claim ("Reserve Bank of India", "PM-KISAN") to a knowledge-graph entity so a person can see what
the system took them to mean. It is context, never evidence: it cannot decide a verdict, and the top search hit can be the wrong
entity, which is why the description is always shown beside it.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import Callable

import httpx

from ..cache import TTLCache
from ..config import Config
from ..errors import RateLimited, SchemaChanged, Unreachable
from ..ratelimit import Spacer
from .probe import build_user_agent

SOURCE_ID = "wikidata"


@dataclass
class Entity:
    name: str                # the name as it appeared in the claim
    id: str                  # the Wikidata item id
    label: str
    description: str
    url: str


class WikidataClient:
    def __init__(
        self,
        config: Config,
        cache: TTLCache,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._src = config.source(SOURCE_ID)
        self._settings = config.runtime.knowledge_graph
        self._cache = cache
        self._http = client or httpx.Client(timeout=self._settings.timeout_s, headers={"User-Agent": build_user_agent(config.runtime.probe.user_agent)})
        self._spacer = Spacer(self._src.rate_limit_per_min, clock, sleep)

    def search(self, name: str) -> Entity | None:
        query = " ".join(name.split())
        if not query or len(query) > self._settings.name_max_chars:
            return None
        key = hashlib.sha256(f"{query.casefold()}|{self._settings.language}".encode("utf-8")).hexdigest()
        hit = self._cache.get(key, self._src.cache_ttl_s)
        if hit is not None:
            return self._first(query, json.loads(hit[0]))
        params = {"action": "wbsearchentities", "format": "json", "search": query, "language": self._settings.language,
                  "uselang": self._settings.language, "limit": 1, "type": "item"}
        self._spacer.wait()
        try:
            response = self._http.get(self._settings.endpoint, params=params)
        except httpx.TimeoutException:
            raise Unreachable(SOURCE_ID, "timed out") from None
        except httpx.HTTPError as exc:
            raise Unreachable(SOURCE_ID, type(exc).__name__) from None
        code = response.status_code
        if code == HTTPStatus.TOO_MANY_REQUESTS:
            raise RateLimited(SOURCE_ID, "HTTP 429")
        if code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            raise Unreachable(SOURCE_ID, f"HTTP {code}")
        if code != HTTPStatus.OK:
            raise SchemaChanged(SOURCE_ID, f"HTTP {code}")
        try:
            body = response.json()
        except ValueError:
            raise SchemaChanged(SOURCE_ID, "response is not JSON") from None
        entity = self._first(query, body)
        self._cache.put(key, json.dumps(body))
        return entity

    def _first(self, name: str, body: object) -> Entity | None:
        results = body.get("search") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise SchemaChanged(SOURCE_ID, "response has no 'search' list")
        if not results:
            return None
        top = results[0]
        if not isinstance(top, dict) or not isinstance(top.get("id"), str):
            raise SchemaChanged(SOURCE_ID, "a search result has no id")
        return Entity(name, top["id"], str(top.get("label", "")), str(top.get("description", "")), self._settings.entity_url_template.format(id=top["id"]))
