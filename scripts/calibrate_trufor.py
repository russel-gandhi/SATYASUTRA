"""Choose the TruFor manipulation cut from data.

    python scripts/calibrate_trufor.py measure --images 30     # needs forensics/service.py running; uses live Wikimedia Commons photos
    python scripts/calibrate_trufor.py write --value 0.5 --note "..."

`measure` draws random Commons photos (almost all unedited), scores them with TruFor, then scores copies that this script edits
itself: a patch spliced in from a different photo, and a region copied within the same photo (copy-move), both saved as JPEG
the way a share would. It also scores recompressed, shrunk copies of the untouched photos, because that is what a post looks like.
For each candidate cut it prints the false-alarm rate on untouched photos and the detection rate on the edits.
The edits are made by this script to measure the model; they are not product data and are never served.
"""

from __future__ import annotations

import argparse
import io
import random
import sys
from datetime import date
from pathlib import Path

import httpx
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from measure_image_match import commons_images  # noqa: E402
from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.sources.probe import build_user_agent  # noqa: E402

THRESHOLD_NAME = "trufor_manipulation_score"
SEED = 7
PATCH_FRACTION = 0.3                 # side of the spliced or copied patch, as a share of the image side
SHARE_QUALITY = 80
SHARE_SCALE = 0.7
CUTS = [i / 10 for i in range(1, 10)]


def jpeg(img: Image.Image, quality: int = SHARE_QUALITY) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=quality)
    return out.getvalue()


def splice(base: Image.Image, donor: Image.Image, rng: random.Random) -> Image.Image:
    img = base.convert("RGB").copy()
    w, h = img.size
    pw, ph = int(w * PATCH_FRACTION), int(h * PATCH_FRACTION)
    patch = donor.convert("RGB").resize((pw, ph))
    img.paste(patch, (rng.randint(0, w - pw), rng.randint(0, h - ph)))
    return img


def copy_move(base: Image.Image, rng: random.Random) -> Image.Image:
    img = base.convert("RGB").copy()
    w, h = img.size
    pw, ph = int(w * PATCH_FRACTION), int(h * PATCH_FRACTION)
    sx, sy = rng.randint(0, w - pw), rng.randint(0, h - ph)
    patch = img.crop((sx, sy, sx + pw, sy + ph))
    img.paste(patch, (rng.randint(0, w - pw), rng.randint(0, h - ph)))
    return img


def share(img: Image.Image) -> Image.Image:
    return Image.open(io.BytesIO(jpeg(img.resize((max(1, int(img.width * SHARE_SCALE)), max(1, int(img.height * SHARE_SCALE)))))))


def score(http: httpx.Client, url: str, data: bytes) -> float | None:
    r = http.post(f"{url}/analyze", content=data, headers={"Content-Type": "application/octet-stream"})
    return r.json()["score"] if r.status_code == httpx.codes.OK else None


def measure(images: int) -> int:
    cfg = load_config()
    url = cfg.runtime.forensics.url
    rng = random.Random(SEED)
    with httpx.Client(timeout=cfg.runtime.forensics.timeout_s, headers={"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}) as http:
        photos = [(t, Image.open(io.BytesIO(b))) for t, b in commons_images(cfg, http, images)]
        print(f"{len(photos)} Commons photos drawn")
        groups: dict[str, list[float]] = {"untouched": [], "untouched, shared (shrunk + JPEG)": [], "spliced, shared": [], "copy-moved, shared": []}
        for i, (title, img) in enumerate(photos):
            donor = photos[(i + 1) % len(photos)][1]
            variants = [("untouched", jpeg(img, 95)), ("untouched, shared (shrunk + JPEG)", jpeg(share(img))),
                        ("spliced, shared", jpeg(share(splice(img, donor, rng)))), ("copy-moved, shared", jpeg(share(copy_move(img, rng))))]
            for name, data in variants:
                s = score(http, url, data)
                if s is not None:
                    groups[name].append(s)
            print(f"  {i + 1}/{len(photos)} " + "  ".join(f"{groups[n][-1]:.2f}" if groups[n] else "-" for n in groups), flush=True)
    print()
    for name, vals in groups.items():
        vals.sort()
        if vals:
            print(f"{name:38s} n={len(vals):3d}  min {vals[0]:.2f}  median {vals[len(vals) // 2]:.2f}  max {vals[-1]:.2f}")
    print(f"\n{'cut':>5s} {'false alarms: untouched':>25s} {'untouched, shared':>19s} {'detected: spliced':>19s} {'copy-moved':>11s}")
    for cut in CUTS:
        row = [sum(v >= cut for v in groups[n]) / max(len(groups[n]), 1) for n in groups]
        print(f"{cut:5.1f} {row[0]:25.0%} {row[1]:19.0%} {row[2]:19.0%} {row[3]:11.0%}")
    return 0


def write(value: float, note: str) -> int:
    target = ROOT / "config" / "thresholds.yaml"
    text = target.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    data["thresholds"] = (data.get("thresholds") or {}) | {THRESHOLD_NAME: {"value": value, "calibrated_on": note, "date": date.today().isoformat()}}
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    target.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"wrote {THRESHOLD_NAME}={value} to {target.relative_to(ROOT)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--images", type=int, default=30)
    w = sub.add_parser("write")
    w.add_argument("--value", type=float, required=True)
    w.add_argument("--note", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    return measure(args.images) if args.cmd == "measure" else write(args.value, args.note)


if __name__ == "__main__":
    raise SystemExit(main())
