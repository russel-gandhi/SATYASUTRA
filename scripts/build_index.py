"""Fill the debunked-image index from the live fact-checker feeds (ticket F1-10).

    python scripts/build_index.py

Re-running adds only new items. Images are downloaded, hashed and discarded; only hashes and links are stored.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.provenance import ImageIndex  # noqa: E402
from satyasutra.provenance.build import build_index  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    cfg = load_config()
    db = Path(cfg.runtime.provenance.db_path)
    index = ImageIndex(db if db.is_absolute() else ROOT / db)
    before = index.count()
    stats = build_index(cfg, index)
    print(f"{'feed':20s} {'articles':>8s} {'images':>7s} {'added':>6s} {'known':>6s} {'small':>6s} {'skipped':>8s} {'failed':>7s}")
    for f in stats.feeds:
        print(f"{f.source_id:20s} {f.articles:8d} {f.images_seen:7d} {f.added:6d} {f.already_indexed:6d} {f.too_small:6d} {f.not_an_image:8d} {f.failed:7d}  {f.error}")
    print(f"\nindex: {before} -> {index.count()} items ({stats.added} added)")
    return 1 if all(f.error for f in stats.feeds) else 0


if __name__ == "__main__":
    raise SystemExit(main())
