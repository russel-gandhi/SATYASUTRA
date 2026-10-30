"""Choose the image-match distance from data (ticket F1-10).

    python scripts/measure_image_match.py measure            # live Wikimedia Commons sample -> table of false matches and recall
    python scripts/measure_image_match.py write --value 31 --note "..."    # record image_match_pdq_distance in config/thresholds.yaml

`measure` draws random Commons images (unrelated to each other, apart from the odd same-series pair, which it prints for a human
to judge), hashes them, and reports for each PDQ distance (a) how many of the unrelated pairs would match falsely and (b) how
many edited copies of the same image would still match. Edits: resize, JPEG recompression, horizontal flip, centre crop.
Images are held in memory and discarded.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from datetime import date
from itertools import combinations
from pathlib import Path

import httpx
import yaml
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.provenance import fingerprint, hamming  # noqa: E402
from satyasutra.provenance.fingerprint import PDQ_BITS, decode  # noqa: E402
from satyasutra.ratelimit import Spacer  # noqa: E402
from satyasutra.sources.probe import build_user_agent  # noqa: E402

NEWLINE = chr(10)
FILE_NAMESPACE = 6                     # MediaWiki namespace for files
ACCEPT = ("image/jpeg", "image/png")
RESIZE_FRACTION = 0.5
JPEG_QUALITY = 40
CROP_FRACTION = 0.9
CLOSEST_SHOWN = 6
TABLE_STEP = 4
BATCH = 50
MAX_ROUNDS = 20


def commons_images(cfg, http: httpx.Client, wanted: int) -> list[tuple[str, bytes]]:
    c = cfg.runtime.provenance.commons
    spacer = Spacer(cfg.source("wikimedia_commons").rate_limit_per_min, time.time, time.sleep)
    out: list[tuple[str, bytes]] = []
    skipped = [0]
    seen: set[str] = set()                       # the random generator can return one file twice; a file is never paired with itself
    for round_number in range(MAX_ROUNDS):
        if len(out) >= wanted:
            break
        spacer.wait()
        # a different batch size each round: Wikimedia caches an identical random request and returns the same files
        params = {"action": "query", "format": "json", "generator": "random", "grnnamespace": FILE_NAMESPACE, "grnlimit": BATCH - round_number,
                  "prop": "imageinfo", "iiprop": "url|mime", "iiurlwidth": c.thumb_width}
        r = http.get(c.endpoint, params=params)
        r.raise_for_status()
        for page in r.json().get("query", {}).get("pages", {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            if info.get("mime") not in ACCEPT or not info.get("thumburl") or page["title"] in seen:
                continue
            seen.add(page["title"])
            spacer.wait()
            try:
                img = http.get(info["thumburl"])
            except httpx.HTTPError:
                skipped[0] += 1                      # a slow or failed thumbnail is skipped and reported, not retried
                continue
            if img.status_code == httpx.codes.OK and len(out) < wanted:
                out.append((page["title"], img.content))
            else:
                skipped[0] += 1
        print(f"  {len(out)}/{wanted} images ({skipped[0]} thumbnails skipped)", flush=True)
    if len(out) < wanted:
        print(f"only {len(out)} of {wanted} images could be drawn; the table below is for that smaller sample")
    return out


def edits(data: bytes) -> dict[str, bytes]:
    image = decode(data)

    def save(im, fmt, **kw):
        buf = io.BytesIO()
        im.save(buf, fmt, **kw)
        return buf.getvalue()

    w, h = image.size
    cw, ch = int(w * CROP_FRACTION), int(h * CROP_FRACTION)
    left, top = (w - cw) // 2, (h - ch) // 2
    return {
        "resize": save(image.resize((int(w * RESIZE_FRACTION), int(h * RESIZE_FRACTION)), Image.Resampling.LANCZOS), "PNG"),
        "jpeg": save(image, "JPEG", quality=JPEG_QUALITY),
        "flip": save(ImageOps.mirror(image), "PNG"),
        "crop": save(image.crop((left, top, left + cw, top + ch)), "PNG"),
    }


def pair_distance(a, b) -> int:
    return min(hamming(a.pdq, b.pdq), hamming(a.pdq_flipped, b.pdq))


def measure() -> int:
    cfg = load_config()
    wanted = cfg.runtime.provenance.commons.sample_size
    headers = {"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}
    with httpx.Client(timeout=cfg.runtime.provenance.download_timeout_s, headers=headers, follow_redirects=True) as http:
        print(f"drawing {wanted} random Commons images ...")
        images = commons_images(cfg, http, wanted)
    prints = [(title, fingerprint(data), data) for title, data in images]

    pairs = sorted(((pair_distance(a[1], b[1]), a[0], b[0]) for a, b in combinations(prints, 2)))
    print(f"{len(prints)} images, {len(pairs)} unrelated pairs. closest pairs (a human should check whether they are really the same picture):")
    for d, x, y in pairs[:CLOSEST_SHOWN]:
        print(f"  {d:3d}  {x[:55]}  |  {y[:55]}")

    edit_distance: dict[str, list[int]] = {}
    for _, fp, data in prints:
        for name, edited in edits(data).items():
            edit_distance.setdefault(name, []).append(pair_distance(fp, fingerprint(edited)))
    print(f"{NEWLINE}distance  false matches (of {len(pairs)} pairs)  " + "  ".join(f"{n:>6s}" for n in edit_distance) + "   (share of edited copies still matched)")
    for t in range(0, PDQ_BITS // 2, TABLE_STEP):
        false = sum(1 for d, _, _ in pairs if d <= t)
        recall = "  ".join(f"{sum(1 for d in ds if d <= t) / len(ds):6.0%}" for ds in edit_distance.values())
        print(f"  {t:3d}     {false:5d} ({false / len(pairs):.3%})            {recall}")
    print(f"{NEWLINE}largest edit distance seen: " + ", ".join(f"{n}={max(ds)}" for n, ds in edit_distance.items()))
    print(f"smallest unrelated-pair distance: {pairs[0][0]}")
    return 0


def write(value: int, note: str) -> int:
    target = ROOT / "config" / "thresholds.yaml"
    text = target.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    data["thresholds"] = (data.get("thresholds") or {}) | {"image_match_pdq_distance": {"value": value, "calibrated_on": note, "date": date.today().isoformat()}}
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    target.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"wrote image_match_pdq_distance={value} to {target.relative_to(ROOT)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("measure")
    w = sub.add_parser("write")
    w.add_argument("--value", type=int, required=True)
    w.add_argument("--note", required=True)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    return measure() if args.cmd == "measure" else write(args.value, args.note)


if __name__ == "__main__":
    raise SystemExit(main())
