"""Smoke test of /video/check on a clip built from live Commons photos, two of whose frames this script splices.

    python scripts/video_live.py [--base http://127.0.0.1:8000]

The clip is a slideshow (one photo per second) made with ffmpeg, so which frames were edited is known: seconds 2 and 5.
It exercises sampling, TruFor on the GPU and the report; it is not a measurement of detection on real-world video.
"""

from __future__ import annotations

import argparse
import io
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from calibrate_trufor import splice  # noqa: E402
from measure_image_match import commons_images  # noqa: E402
from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.sources.probe import build_user_agent  # noqa: E402

SECONDS = 8
EDITED_SECONDS = (2, 5)
SIZE = (640, 480)
SEED = 5


def build_clip(photos: list[Image.Image], folder: Path) -> Path:
    rng = random.Random(SEED)
    for i in range(SECONDS):
        img = photos[i % len(photos)]
        if i in EDITED_SECONDS:
            img = splice(img, photos[(i + 1) % len(photos)], rng)
        img.convert("RGB").resize(SIZE).save(folder / f"f{i:03d}.png")
    out = folder / "clip.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-framerate", "1", "-i", str(folder / "f%03d.png"), "-r", "10", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    cfg = load_config()
    with httpx.Client(timeout=cfg.runtime.forensics.timeout_s * 2, headers={"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}) as http, \
            tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        photos = [Image.open(io.BytesIO(b)) for _, b in commons_images(cfg, http, SECONDS)]
        clip = build_clip(photos, Path(tmp))
        print(f"clip {clip.stat().st_size // 1024} KiB, {SECONDS} s; frames edited at seconds {EDITED_SECONDS}")
        print("the same edited frames as still images, before video compression:")
        for i in EDITED_SECONDS:
            still = http.post(f"{args.base}/media/check", content=(Path(tmp) / f"f{i:03d}.png").read_bytes()).json()
            print(f"  second {i}: score {still['forensics'].get('score')}  {still['label']}")
        r = http.post(f"{args.base}/video/check", content=clip.read_bytes())
        if r.status_code != httpx.codes.OK:
            print("HTTP", r.status_code, r.text[:300])
            return 1
        body = r.json()
        print(f"label {body['label']}  duration {body['duration_s']} s")
        for f in body["frames"]:
            print(f"  {f['at_s']:5.2f} s  score {f['score'] if f['score'] is None else round(f['score'], 2)}  {'FLAGGED' if f['flagged'] else ''}")
        for reason in body["reasons"]:
            print("  -", reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
