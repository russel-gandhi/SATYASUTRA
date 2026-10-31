"""Is the demo machine ready? One table, green or not, before anyone walks on stage.

    python scripts/preflight.py                 # checks keys by NAME, config, every source, the TruFor+Smaug service, ffmpeg, indexes, ledger
    python scripts/preflight.py --api http://127.0.0.1:8000

Exit code 0 when nothing failed (warnings are allowed), 1 otherwise. Key values are never read into the output: only whether a name is set.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.ledger import SQLiteLedgerStore  # noqa: E402
from satyasutra.provenance import ImageIndex  # noqa: E402
from satyasutra.sources import probe_all  # noqa: E402
from satyasutra.sources.index import OfficialIndex  # noqa: E402

REQUIRED_KEYS = ["GEMINI_API_KEY", "GOOGLE_FACTCHECK_API_KEY", "FORGE_CONTACT"]
OPTIONAL_KEYS = ["DATA_GOV_IN_API_KEY", "TELEGRAM_BOT_TOKEN"]
NEEDED_SOURCES = {"gemini_llm", "factcheck_tools", "wikidata", "bluesky_jetstream", "rbi_press", "sebi_feed", "wikimedia_commons"}
OK, WARN, FAIL = "ok", "WARN", "FAIL"


def path_of(cfg_value: str) -> Path:
    p = Path(cfg_value)
    return p if p.is_absolute() else ROOT / p


def run(api: str | None) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    add = lambda state, name, detail: rows.append((state, name, detail))
    load_env_file(ROOT / ".env")
    for k in REQUIRED_KEYS:
        add(OK if os.environ.get(k) else FAIL, f"key {k}", "set" if os.environ.get(k) else "not set in .env")
    for k in OPTIONAL_KEYS:
        add(OK if os.environ.get(k) else WARN, f"key {k}", "set" if os.environ.get(k) else "not set (optional)")
    try:
        cfg = load_config()
        add(OK, "config", f"loaded, checksum {cfg.checksum[:12]}")
    except Exception as exc:
        add(FAIL, "config", type(exc).__name__)
        return rows

    for r in probe_all(cfg):
        needed = r.source_id in NEEDED_SOURCES
        state = OK if r.state == "reachable" else (FAIL if needed else WARN)
        add(state, f"source {r.source_id}", f"{r.state}" + (f" ({r.http_status})" if r.http_status else "") + ("" if needed else " - not needed for the demo"))

    f = cfg.runtime.forensics
    try:
        h = httpx.get(f"{f.url}/health", timeout=5).json()
        add(OK, "TruFor service", f"up on {h.get('device')}")
        ai = h.get("ai_detector")
        add(OK if ai == "loaded" else WARN, "AI-image detector (Smaug)", ai or "service predates the health field; restart it")
    except Exception as exc:
        add(FAIL, "TruFor service", f"{type(exc).__name__}: start forensics/venv311/Scripts/python forensics/service.py --port 8100")
    for tool in ("ffmpeg", "ffprobe"):
        add(OK if shutil.which(tool) else FAIL, tool, shutil.which(tool) or "not on PATH (video check needs it)")

    img = path_of(cfg.runtime.provenance.db_path)
    n = ImageIndex(img).count() if img.exists() else 0
    add(OK if n else WARN, "debunked-image index", f"{n} items" + ("" if n else " - run scripts/build_index.py"))
    off = path_of(cfg.runtime.official.db_path)
    m = OfficialIndex(off).count() if off.exists() else 0
    add(OK if m else WARN, "official-text index", f"{m} passages" + ("" if m else " - fills on the first claim check"))
    led = path_of(cfg.runtime.ledger_db_path)
    if led.exists():
        v = SQLiteLedgerStore(led, code_version="preflight").verify()
        add(OK if v.ok else FAIL, "ledger", f"{'chain intact' if v.ok else 'BROKEN at ' + str(v.first_bad_seq)}, {v.checked} entries")
    else:
        add(WARN, "ledger", "no ledger yet (created on the first verdict)")
    if api:
        try:
            health = httpx.get(f"{api}/health", timeout=5).json()
            add(OK, "API /health", f"version {health.get('version')}, {health.get('ledger_entries')} ledger entries")
            stats = httpx.get(f"{api}/fastclock/stats", timeout=5)
            add(OK if stats.status_code == httpx.codes.OK and stats.json().get("running") else WARN, "live stream worker", "running" if stats.status_code == httpx.codes.OK and stats.json().get("running") else "not running")
        except Exception as exc:
            add(FAIL, "API", f"{type(exc).__name__}: start scripts/serve.py")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=None, help="base URL of a running API to check as well")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows = run(args.api)
    width = max(len(n) for _, n, _ in rows)
    for state, name, detail in rows:
        print(f"{state:5s} {name.ljust(width)}  {detail}")
    fails = sum(s == FAIL for s, _, _ in rows)
    print(f"\n{fails} failed, {sum(s == WARN for s, _, _ in rows)} warnings")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
