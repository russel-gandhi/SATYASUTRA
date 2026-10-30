"""Run the walking skeleton on posts taken live from the stream and print what each stage decided (ticket F1-09).

    python scripts/verify_live.py --posts 8
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from satyasutra import __version__  # noqa: E402
from satyasutra.api.app import create_app  # noqa: E402
from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.ingest import IngestStats, stream_posts  # noqa: E402
from satyasutra.ledger import SQLiteLedgerStore  # noqa: E402

PREVIEW = 110


async def take(cfg, n):
    found = []
    async for post in stream_posts(cfg.runtime.ingest, IngestStats(), "verify-live-salt"):
        found.append(post)
        if len(found) == n:
            return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--posts", type=int, required=True)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    cfg = load_config()
    with tempfile.TemporaryDirectory() as tmp:
        client = TestClient(create_app(cfg, SQLiteLedgerStore(Path(tmp) / "l.sqlite3", code_version=__version__)))
        for post in asyncio.run(take(cfg, args.posts)):
            r = client.post("/verify", json={"text": post.text})
            print(f"\n[{r.status_code}] {post.text[:PREVIEW]!r}")
            if r.status_code != 200:
                print("   ", r.text[:200])
                continue
            body = r.json()
            print(f"    extraction {body['extraction']['status']}, {len(body['claims'])} claims, {body['extraction']['non_claims']} non-claims")
            for c in body["claims"]:
                print(f"    {c['label']} ({c['confidence_band']}) seq {c['ledger_seq']}: {c['text'][:PREVIEW]!r}; {len(c['citations'])} cited, {len(c['related'])} related")
        print("\nledger:", client.get("/ledger/verify").json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
