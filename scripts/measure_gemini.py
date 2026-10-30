"""Measure what the team's Gemini key can really do (ticket F1-02).

    python scripts/measure_gemini.py                   # latency sample + burst until the first 429 + retest of slow models
    python scripts/measure_gemini.py --write-report    # also writes docs/gemini_limits.md

Total live calls are capped by policy.gemini.measure_max_calls. The key is read from the environment and never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402

REPORT = ROOT / "docs" / "gemini_limits.md"
SECONDS_PER_MINUTE = 60
LATENCY_SAMPLE = 8
BURST_WORKERS = 4
PROMPT = "List the atomic claims in: 'The dam collapsed yesterday in Bihar.' Return JSON."
BODY = {
    "contents": [{"parts": [{"text": PROMPT}]}],
    "generationConfig": {
        "responseMimeType": "application/json",
        "temperature": 0,
        "responseSchema": {
            "type": "OBJECT",
            "properties": {"claims": {"type": "ARRAY", "items": {"type": "STRING"}}},
            "required": ["claims"],
        },
    },
}


@dataclass
class Sample:
    status: int            # HTTP status; 0 means timeout or connection failure
    seconds: float
    tokens: int | None = None
    error: dict = field(default_factory=dict)


def call(base: str, model: str, key: str, timeout: float, client: httpx.Client | None = None) -> Sample:
    started = time.perf_counter()
    try:
        r = (client or httpx).post(f"{base}/models/{model}:generateContent", params={"key": key}, json=BODY, timeout=timeout)
    except httpx.HTTPError:
        return Sample(0, round(time.perf_counter() - started, 2))
    took = round(time.perf_counter() - started, 2)
    try:
        data = r.json()
    except ValueError:
        return Sample(r.status_code, took)
    if r.status_code == 200:
        return Sample(200, took, tokens=(data.get("usageMetadata") or {}).get("totalTokenCount"))
    return Sample(r.status_code, took, error=data.get("error") or {})


def parse_quota_error(error: dict) -> dict:
    """What a 429 body says: message, quota names and values, and the retry delay. Missing fields stay empty."""
    out = {"message": str(error.get("message", ""))[:300], "quotas": [], "retry_delay": ""}
    for d in error.get("details", []) or []:
        kind = str(d.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            for v in d.get("violations", []) or []:
                out["quotas"].append({"metric": v.get("quotaMetric", ""), "id": v.get("quotaId", ""), "value": v.get("quotaValue", "")})
        elif kind.endswith("RetryInfo"):
            out["retry_delay"] = str(d.get("retryDelay", ""))
    return out


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def latency_summary(samples: list[Sample]) -> dict:
    ok = [s.seconds for s in samples if s.status == 200]
    tokens = [s.tokens for s in samples if s.tokens]
    return {
        "n": len(samples),
        "ok": len(ok),
        "median_s": round(statistics.median(ok), 2) if ok else None,
        "p95_s": round(percentile(ok, 95), 2) if ok else None,
        "tokens_per_call": round(statistics.median(tokens)) if tokens else None,
        "statuses": sorted({s.status for s in samples}),
    }


class Budget:
    """A hard cap shared by every phase, so one run can never exceed policy.gemini.measure_max_calls."""

    def __init__(self, cap: int):
        self.cap, self.used, self._lock = cap, 0, threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self.used >= self.cap:
                return False
            self.used += 1
            return True


def burst(one_call, budget: Budget, workers: int = BURST_WORKERS) -> dict:
    """Call as fast as possible until the first 429 or the budget ends."""
    stop = threading.Event()
    results: list[Sample] = []
    lock = threading.Lock()
    t0 = time.perf_counter()

    def worker():
        while not stop.is_set() and budget.take():
            s = one_call()
            with lock:
                results.append(s)
            if s.status == 429:
                stop.set()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in range(workers):
            pool.submit(worker)
    elapsed = time.perf_counter() - t0
    ok = sum(1 for s in results if s.status == 200)
    limited = next((s for s in results if s.status == 429), None)
    return {
        "calls": len(results),
        "ok_before_limit": ok,
        "elapsed_s": round(elapsed, 1),
        "rate_per_min": round(ok / elapsed * SECONDS_PER_MINUTE) if elapsed else 0,
        "hit_429": limited is not None,
        "quota": parse_quota_error(limited.error) if limited else None,
        "other_statuses": sorted({s.status for s in results} - {200, 429}),
    }


def render(model_rows: list[dict], retest: dict, stamp: str, cap: int, used: int) -> str:
    out = [
        "# Gemini limits and latency (ticket F1-02)",
        "",
        f"Measured {stamp} against the live API with the team's key. Calls used: {used} of the {cap} cap.",
        "Per-day quotas cannot be measured without exhausting them; where the API states a quota in a 429 body it is quoted below, otherwise it is **not measured**.",
        "",
        "## Latency, short structured-output call",
        "",
        "| Model | Samples | OK | Median s | p95 s | Tokens per call | Statuses |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in model_rows:
        s = r["latency"]
        out.append(f"| {r['model']} | {s['n']} | {s['ok']} | {s['median_s']} | {s['p95_s']} | {s['tokens_per_call']} | {s['statuses']} |")
    b = model_rows[0]["burst"] if model_rows else None
    if b:
        out += ["", f"## Burst on {model_rows[0]['model']}", "", f"- {b['calls']} calls in {b['elapsed_s']} s, {b['ok_before_limit']} succeeded (about {b['rate_per_min']} per minute at this speed)."]
        if b["hit_429"]:
            q = b["quota"]
            out.append(f"- First 429 reached. Message: {q['message']!r}; retry delay: {q['retry_delay'] or 'not stated'}.")
            for item in q["quotas"]:
                out.append(f"  - quota `{item['id'] or item['metric']}` value {item['value'] or 'not stated'}")
        else:
            out.append("- **No 429 within the cap**: the per-minute limit is above the rate reached here, so it is not measured.")
        if b["other_statuses"]:
            out.append(f"- Other statuses seen: {b['other_statuses']}.")
    out += ["", "## Re-test of slower models", "", "| Model | Attempts | OK | Timeouts | Seconds per OK |", "|---|---|---|---|---|"]
    for model, samples in retest.items():
        ok = [s.seconds for s in samples if s.status == 200]
        out.append(f"| {model} | {len(samples)} | {len(ok)} | {sum(1 for s in samples if s.status == 0)} | {ok or '-'} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write-report", action="store_true")
    args = ap.parse_args(argv)

    load_env_file(ROOT / ".env")
    cfg = load_config()
    gem = cfg.policy.gemini
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("set GEMINI_API_KEY (in .env) first", file=sys.stderr)
        return 2

    budget = Budget(gem.measure_max_calls)
    rows: list[dict] = []
    retest: dict[str, list[Sample]] = {}
    with httpx.Client() as client:
        for i, model in enumerate(gem.models):
            one = lambda m=model: call(gem.api_base, m, key, gem.measure_timeout_s, client)  # noqa: E731
            if i == 0:
                samples = [one() for _ in range(LATENCY_SAMPLE) if budget.take()]
                rows.append({"model": model, "latency": latency_summary(samples), "burst": burst(one, budget)})
                print(f"{model}: {rows[-1]['latency']} burst={rows[-1]['burst']}", flush=True)
            else:
                attempts = []
                for _ in range(gem.retest_attempts):
                    if not budget.take():
                        break
                    attempts.append(one())
                retest[model] = attempts
                print(f"{model}: {[(s.status, s.seconds) for s in attempts]}", flush=True)
    if args.write_report:
        from datetime import datetime, timezone
        REPORT.write_text(render(rows, retest, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), gem.measure_max_calls, budget.used), encoding="utf-8")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
