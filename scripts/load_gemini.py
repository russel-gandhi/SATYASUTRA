"""Gemini load test (package B4): model calls per message by purpose, and what a person sees when several people ask at once.

    python scripts/load_gemini.py --users 1,3,6 --out docs/load_gemini.md

Runs the real app in this process against the live API (needs GEMINI_API_KEY and the Fact Check key in .env), counting every model call by the first words
of its prompt. Free-tier limit: 15 requests a minute per model; with fallback models a message can still be answered when one is busy. The numbers are for this key,
this day and this network. Each user sends a different claim so the verdict cache cannot answer for them.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.api.app import create_app  # noqa: E402
from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.llm import gemini as gem  # noqa: E402

CLAIMS = [
    "RBI has banned all UPI payments above 5000 rupees from next week",
    "The Reserve Bank of India raised the repo rate to 9 per cent in October 2026",
    "A new metro line in Pune collapsed this morning killing twenty people",
    "SEBI has banned all trading in small cap stocks starting Monday",
    "Mumbai airport will be closed for the next three days because of a strike",
    "The government will give free laptops to every student who applies this month",
    "Petrol will cost 200 rupees a litre from the first of next month",
    "India has banned the sale of two thousand rupee notes in all shops",
]
LABEL_WORDS = 6
count_lock, calls, outcomes = Lock(), [], Counter()


def patch() -> None:
    original = gem.GeminiClient.generate_json

    def counted(self, prompt, schema, system=None, images=None):
        purpose = " ".join(prompt.split()[:LABEL_WORDS])
        t = time.perf_counter()
        try:
            reply = original(self, prompt, schema, system, images)
            status = "ok"
            return reply
        except Exception as exc:                                  # counted by class, then raised as before
            status = type(exc).__name__
            raise
        finally:
            with count_lock:
                calls.append((purpose, status, time.perf_counter() - t))
    gem.GeminiClient.generate_json = counted


def one_user(client: TestClient, claim: str) -> dict:
    t = time.perf_counter()
    r = client.get("/agent/stream", params={"text": claim})
    body = r.text
    kind = "answered" if "event: result" in body else ("busy" if (r.status_code == 503 or "ModelBusy" in body) else f"no result (HTTP {r.status_code})")
    return {"seconds": time.perf_counter() - t, "kind": kind}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", default="1,3,6")
    ap.add_argument("--out", default="docs/load_gemini.md")
    args = ap.parse_args()
    load_env_file(ROOT / ".env")
    patch()
    cfg = load_config(ROOT / "config")
    app = create_app(cfg)
    client = TestClient(app)
    lines = [f"# Gemini load test, {time.strftime('%Y-%m-%d %H:%M')} local time", "", f"Command: `python scripts/load_gemini.py --users {args.users}`. Live API, this key and network.", "",
             "## What people saw", "", "| Simultaneous users | Answered | Busy message | Other | Median s | Slowest s | Model calls |", "|---|---|---|---|---|---|---|"]
    used, per_purpose = 0, defaultdict(Counter)
    for n in [int(x) for x in args.users.split(",")]:
        calls.clear()
        picks = CLAIMS[used: used + n]
        used += n
        with ThreadPoolExecutor(max_workers=n) as pool:
            res = list(pool.map(lambda c: one_user(client, c), picks))
        secs = sorted(r["seconds"] for r in res)
        kinds = Counter(r["kind"] for r in res)
        other = n - kinds["answered"] - kinds["busy"]
        lines.append(f"| {n} | {kinds['answered']} | {kinds['busy']} | {other} | {secs[len(secs) // 2]:.1f} | {secs[-1]:.1f} | {len(calls)} ({len(calls) / n:.1f} per message) |")
        for purpose, status, _ in calls:
            per_purpose[purpose][status] += 1
    lines += ["", "## Model calls by purpose (all runs)", "", "| Purpose (start of prompt) | ok | failed (class: count) |", "|---|---|---|"]
    for purpose, c in sorted(per_purpose.items()):
        fails = ", ".join(f"{k}: {v}" for k, v in c.items() if k != "ok") or "none"
        lines.append(f"| {purpose} | {c['ok']} | {fails} |")
    lines += ["", "## Not measured", "", "- More than the listed number of simultaneous users, other days, a paid tier, or the Telegram path (it calls the same API).",
              "- Whether a busy answer was a per-minute or a per-day limit."]
    out = ROOT / args.out
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
