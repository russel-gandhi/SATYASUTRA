"""A small Gemini client for structured output. One call in, one parsed JSON object out, one typed error per way it can fail.

Model, timeout, retry limits and the per-minute budget all come from config/policy.yaml. The key comes from the environment
and is never logged or put in an error message (httpx error text can contain the URL, so only the exception class is kept).
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import Callable, Mapping

import httpx

from ..config import Config
from ..errors import AuthRefused, ConfigError, ContentBlocked, QuotaExhausted, RateLimited, SchemaChanged, Unreachable
from ..ratelimit import Spacer

SOURCE_ID = "gemini_llm"
RETRY_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")
PER_DAY_MARK = "PerDay"


class BadOutput(SchemaChanged):
    """The model answered, but not with a JSON object. Retrying may help, unlike a changed API."""


@dataclass
class Reply:
    data: dict
    model: str
    tokens: int | None
    seconds: float
    attempts: int


def retry_delay_s(error: dict) -> float | None:
    """The retry delay a 429 body asks for, in seconds, or None when it does not say."""
    for d in error.get("details", []) or []:
        if str(d.get("@type", "")).endswith("RetryInfo"):
            m = RETRY_DELAY.match(str(d.get("retryDelay", "")))
            if m:
                return float(m.group(1))
    return None


def is_daily_quota(error: dict) -> bool:
    for d in error.get("details", []) or []:
        if str(d.get("@type", "")).endswith("QuotaFailure"):
            return any(PER_DAY_MARK in str(v.get("quotaId", "")) for v in d.get("violations", []) or [])
    return False


class GeminiClient:
    def __init__(
        self,
        config: Config,
        client: httpx.Client | None = None,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._p = config.policy.gemini
        self._src = config.source(SOURCE_ID)
        self._env, self._sleep = env, sleep
        self._http = client or httpx.Client(timeout=self._p.request_timeout_s)
        self._spacer = Spacer(self._p.requests_per_minute, clock, sleep)
        self.model = self._p.models[0]

    def _key(self) -> str:
        env = os.environ if self._env is None else self._env
        key = env.get(self._src.auth_env or "", "")
        if not key:
            raise ConfigError(f"set {self._src.auth_env} to use {SOURCE_ID}")
        return key

    def generate_json(self, prompt: str, schema: dict, system: str | None = None, images: list[tuple[str, bytes]] | None = None) -> Reply:
        """Try each configured model in order. A model that times out, answers 5xx, is rate limited, out of quota or gone is skipped for the next one
        (free-tier quotas are per model); a bad key or a blocked prompt is not retried anywhere. The reply names the model that answered."""
        key = self._key()
        body: dict = {
            "contents": [{"parts": [{"text": prompt}, *({"inlineData": {"mimeType": mime, "data": base64.b64encode(raw).decode("ascii")}} for mime, raw in images or [])]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema, "temperature": 0},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        first: Exception | None = None
        for model in self._p.models:
            try:
                reply = self._one_model(model, key, body)
                self.model = model
                return reply
            except (Unreachable, RateLimited, QuotaExhausted) as exc:
                first = first or exc
            except SchemaChanged as exc:
                if "not found" not in exc.detail:                 # a model closed to this key is skipped; any other schema change is real
                    raise
                first = first or exc
        assert first is not None
        raise first

    def _one_model(self, model: str, key: str, body: dict) -> Reply:
        url = f"{self._p.api_base}/models/{model}:generateContent"
        attempts = transient = 0
        while True:
            attempts += 1
            self._spacer.wait()
            started = time.perf_counter()
            try:
                response = self._http.post(url, params={self._src.auth_param or "key": key}, json=body)
            except httpx.HTTPError as exc:
                if transient < self._p.transient_retries:
                    transient += 1
                    self._sleep(self._p.transient_backoff_s)
                    continue
                if isinstance(exc, httpx.TimeoutException):
                    raise Unreachable(SOURCE_ID, "timed out") from None
                raise Unreachable(SOURCE_ID, type(exc).__name__) from None
            took = round(time.perf_counter() - started, 2)

            if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
                try:
                    error = response.json().get("error", {})
                except ValueError:
                    error = {}
                if is_daily_quota(error):
                    raise QuotaExhausted(SOURCE_ID, "daily quota used up")
                wait = retry_delay_s(error)
                if wait is None or wait > self._p.max_retry_wait_s or attempts > self._p.max_retries:
                    raise RateLimited(SOURCE_ID, f"HTTP 429, retry delay {wait if wait is not None else 'not stated'}")
                self._sleep(wait)
                continue
            if response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR and transient < self._p.transient_retries:
                transient += 1
                self._sleep(self._p.transient_backoff_s)
                continue
            return self._parse(response, took, attempts, model)

    def _parse(self, response: httpx.Response, took: float, attempts: int, model: str) -> Reply:
        code = response.status_code
        if code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            raise AuthRefused(SOURCE_ID, f"HTTP {code}")
        if code == HTTPStatus.NOT_FOUND:
            raise SchemaChanged(SOURCE_ID, f"model {model} not found or closed to new keys")
        if code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            raise Unreachable(SOURCE_ID, f"HTTP {code}")
        if code != HTTPStatus.OK:
            raise SchemaChanged(SOURCE_ID, f"HTTP {code}: request not accepted")
        try:
            data = response.json()
        except ValueError:
            raise SchemaChanged(SOURCE_ID, "response is not JSON") from None

        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise ContentBlocked(SOURCE_ID, f"prompt blocked: {block}")
        candidates = data.get("candidates") or []
        if not candidates:
            raise SchemaChanged(SOURCE_ID, "no candidates in the response")
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
        if not text:
            reason = candidate.get("finishReason", "unknown")
            raise ContentBlocked(SOURCE_ID, f"no output (finishReason {reason})")
        try:
            parsed = json.loads(text)
        except ValueError:
            raise BadOutput(SOURCE_ID, "model output is not valid JSON") from None
        if not isinstance(parsed, dict):
            raise BadOutput(SOURCE_ID, "model output is not a JSON object")
        return Reply(parsed, model, (data.get("usageMetadata") or {}).get("totalTokenCount"), took, attempts)
