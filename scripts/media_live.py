"""Send live Commons photos to a running /media/check: one untouched and one with a patch spliced in from another photo.

    python scripts/media_live.py [--base http://127.0.0.1:8000]

A smoke test of the whole media path (upload, fingerprint, debunked-image index, TruFor on the GPU). The splice is made by this
script to exercise the detector; it is never stored or served.
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

from calibrate_trufor import jpeg, splice  # noqa: E402
from measure_image_match import commons_images  # noqa: E402
from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.sources.probe import build_user_agent  # noqa: E402

SEED = 11
WANTED = 2


def show(name: str, body: dict) -> None:
    f = body["forensics"]
    score = f"{f['score']:.2f}" if f.get("available") else "n/a"
    print(f"{name:10s} label {body['label']:13s} score {score}  {f.get('seconds', '-')} s on {f.get('device', '-')}  "
          f"matches {len(body['matches'])}  heatmap {'yes' if body['heatmap_png_b64'] else 'no'}")
    for r in body["reasons"]:
        print(f"           - {r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=load_config().runtime.forensics.url.rsplit(":", 1)[0] + ":8000")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    cfg = load_config()
    with httpx.Client(timeout=cfg.runtime.forensics.timeout_s, headers={"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}) as http:
        photos = [Image.open(io.BytesIO(b)) for _, b in commons_images(cfg, http, WANTED)]
        edited = splice(photos[0], photos[1], random.Random(SEED))
        for name, img in (("untouched", photos[0]), ("spliced", edited)):
            r = http.post(f"{args.base}/media/check", content=jpeg(img, 90))
            if r.status_code != httpx.codes.OK:
                print(name, "HTTP", r.status_code, r.text[:200])
                return 1
            show(name, r.json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
