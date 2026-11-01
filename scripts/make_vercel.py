"""Write deploy/vercel/vercel.json: a Vercel front door that proxies the public pages and APIs to the machine that runs SATYASUTRA.

Why a proxy: the app needs the GPU service, SQLite files and a long-lived stream, none of which fit Vercel's serverless functions. Vercel gives the
public https address; the work happens on the backend you pass in. The command centre, intel, registry and case pages are NOT listed, so they
answer 404 on the public address (the app has no login and those pages show every stored record).

    python scripts/make_vercel.py https://your-backend.example
    cd deploy/vercel && vercel deploy --prod
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PUBLIC = [
    "/", "/check/:kind", "/health", "/health/sources", "/ledger/verify", "/ledger/:seq", "/fastclock/:p*", "/notice/check", "/url/check",
    "/doc/check", "/pulse", "/pulse/match", "/officials", "/officials/data", "/guard", "/guard/:p*", "/links/check", "/voice/transcribe",
    "/report/build", "/report/:rid", "/my-investigations", "/history", "/records/:rid", "/help", "/telegram", "/telegram/info", "/raksha/:p*", "/news/check", "/audio/check",
    "/card/spec", "/act/plan", "/media/check", "/video/check", "/agent/stream", "/trace/stream", "/verify", "/about", "/assets/:p*", "/static/:p*",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("backend", help="https address of the running SATYASUTRA server, no trailing slash")
    backend = ap.parse_args().backend.rstrip("/")
    if not backend.startswith("https://"):
        print("the backend must be an https address", file=sys.stderr)
        return 2
    out = Path(__file__).resolve().parents[1] / "deploy" / "vercel"
    out.mkdir(parents=True, exist_ok=True)
    rewrites = [{"source": src, "destination": backend + src} for src in PUBLIC]
    (out / "vercel.json").write_text(json.dumps({"rewrites": rewrites}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out / 'vercel.json'} with {len(rewrites)} proxied routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
