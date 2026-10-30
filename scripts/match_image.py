"""Look an image up in the debunked-image index (ticket F1-10).

    python scripts/match_image.py <file-or-url>

Prints the nearest indexed items with their Hamming distances, or says there is no match. A match means the image appeared in a
fact-checker's article; it is a lead for provenance, not a verdict on any claim. The image is hashed and discarded.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.provenance import BadImage, ImageIndex, fingerprint  # noqa: E402
from satyasutra.provenance.feeds import download_image  # noqa: E402
from satyasutra.sources.probe import build_user_agent  # noqa: E402

SCHEMES = ("http://", "https://")  # forge: allow-literal URL scheme prefixes, not a source


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="a local file path or an http(s) URL")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    cfg = load_config()
    prov = cfg.runtime.provenance

    if args.image.startswith(SCHEMES):
        with httpx.Client(timeout=prov.download_timeout_s, headers={"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}, follow_redirects=True) as http:
            data = download_image(http, args.image, prov.max_image_bytes, "match_image")
        if data is None:
            print("could not download an image from that URL (missing, not an image, or too large)")
            return 2
    else:
        data = Path(args.image).read_bytes()
    try:
        fp = fingerprint(data)
    except BadImage as exc:
        print(f"not an image: {exc}")
        return 2

    cut = cfg.thresholds.thresholds["image_match_pdq_distance"].value
    db = Path(prov.db_path)
    index = ImageIndex(db if db.is_absolute() else ROOT / db)
    hits = index.nearest(fp, cut, prov.match_limit)
    print(f"{fp.width}x{fp.height}, PDQ quality {fp.pdq_quality}; index holds {index.count()} items; match distance <= {cut} bits")
    if not hits:
        print("no match: this image is not in the index. That says nothing about whether it is authentic.")
        return 0
    for h in hits:
        how = "byte-identical" if h.exact else ("mirrored" if h.mirrored else "")
        print(f"\n  distance {h.distance:3d} (pHash {h.phash_distance:2d}) {how}")
        print(f"    {h.source.publisher} ({h.source.lang}), reviewed {h.source.reviewed_at.date() if h.source.reviewed_at else 'unknown'}: {h.source.title}")
        print(f"    article {h.source.article_url}")
        print(f"    labels  {h.source.labels or '-'}   (feed categories, not a rating)   indexed {h.retrieved_at.date()}")
    print("\nA match means this image appeared in a fact-checker's article. It does not say the claim is false.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
