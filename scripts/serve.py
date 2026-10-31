"""Serve SATYASUTRA with the live stream worker on: UI at /, API beside it.

    python scripts/serve.py [--port 8000] [--escalate]

config/runtime.yaml keeps the worker off so tests never touch the network; this script turns it on for a running demo.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.api.app import create_app  # noqa: E402
from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--escalate", action="store_true", help="also audit the busiest clusters automatically (spends Gemini requests)")
    args = ap.parse_args()
    load_env_file(ROOT / ".env")
    cfg = load_config()
    runtime = cfg.runtime.model_copy(update={
        "fastclock": cfg.runtime.fastclock.model_copy(update={"worker_enabled": True}),
        "escalation": cfg.runtime.escalation.model_copy(update={"enabled": args.escalate}),
    })
    cfg = cfg.model_copy(update={"runtime": runtime})
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
