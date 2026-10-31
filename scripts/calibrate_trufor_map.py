"""Does TruFor's localisation map catch the edits its image-level score misses?

Same recipe as calibrate_trufor.py (Commons photos, a self-made splice or copy-move of 30% of the side, shrunk to 70% and JPEG 80), but records the
map statistics the service already returns (map_mean, map_p95) next to the score, then prints, for each candidate map cut, how many untouched
and how many edited photos it would add to the ones the score cut already flags.

    python scripts/calibrate_trufor_map.py --images 30 > docs/calibration/trufor_map_<date>.txt
"""

from __future__ import annotations

import argparse
import io
import random
import sys
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from calibrate_trufor import SEED, copy_move, jpeg, share, splice  # noqa: E402
from measure_image_match import commons_images  # noqa: E402
from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.sources.probe import build_user_agent  # noqa: E402

GROUPS = ("untouched, shared", "spliced, shared", "copy-moved, shared")
MAP_CUTS = [0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5]


def analyse(http: httpx.Client, url: str, data: bytes) -> dict | None:
    r = http.post(f"{url}/analyze", content=data, headers={"Content-Type": "application/octet-stream"})
    return r.json() if r.status_code == httpx.codes.OK else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", type=int, default=30)
    n = ap.parse_args().images
    load_env_file(ROOT / ".env")
    cfg = load_config()
    score_cut = cfg.thresholds.thresholds["trufor_manipulation_score"].value
    rng = random.Random(SEED)
    rows: dict[str, list[dict]] = {g: [] for g in GROUPS}
    with httpx.Client(timeout=cfg.runtime.forensics.timeout_s, headers={"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}) as http:
        photos = [Image.open(io.BytesIO(b)) for _, b in commons_images(cfg, http, n)]
        print(f"{len(photos)} Commons photos; score cut in use {score_cut}")
        for i, img in enumerate(photos):
            donor = photos[(i + 1) % len(photos)]
            variants = {GROUPS[0]: jpeg(share(img)), GROUPS[1]: jpeg(share(splice(img, donor, rng))), GROUPS[2]: jpeg(share(copy_move(img, rng)))}
            for name, data in variants.items():
                a = analyse(http, cfg.runtime.forensics.url, data)
                if a and a.get("map_p95") is not None:
                    rows[name].append(a)
    for g, rs in rows.items():
        print(f"\n{g}: n={len(rs)}")
        print("  score   " + " ".join(f"{r['score']:.2f}" for r in rs))
        print("  map_p95 " + " ".join(f"{r['map_p95']:.2f}" for r in rs))
        print("  map_mean" + " ".join(f"{r['map_mean']:.2f}" for r in rs))
    print(f"\nflagged by score >= {score_cut} alone, then flagged by score OR map_p95 >= cut / map_mean >= cut")
    print(f"{'cut':>5s} {'stat':>9s} " + " ".join(f"{g[:18]:>20s}" for g in GROUPS))
    base = [sum(r["score"] >= score_cut for r in rows[g]) / max(len(rows[g]), 1) for g in GROUPS]
    print(f"{'-':>5s} {'score':>9s} " + " ".join(f"{b:20.0%}" for b in base))
    for stat in ("map_p95", "map_mean"):
        for cut in MAP_CUTS:
            fr = [sum(r["score"] >= score_cut or r[stat] >= cut for r in rows[g]) / max(len(rows[g]), 1) for g in GROUPS]
            print(f"{cut:5.2f} {stat:>9s} " + " ".join(f"{f:20.0%}" for f in fr))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
