"""Score a labelled image folder with the Smaug AI-image detector. Run with forensics\\venv311.

    forensics\\venv311\\Scripts\\python -I forensics\\smaug_probe.py <image_dir>

Our own loader: the preprocessing below follows the model card (shorter side to 576, 528 px crops, 140 vision tokens) and the
model is loaded through sentence-transformers from the downloaded weights. The repo's own .py file is not imported or run.
"""

import json
import math
import sys
from pathlib import Path

import torch
from PIL import Image, ImageOps
from sentence_transformers import SentenceTransformer

MODEL = Path(__file__).resolve().parent / "models" / "smaug"
CROP, SIDE, BUDGET = 528, 576, 140


def views(img):
    img = ImageOps.exif_transpose(img).convert("RGB")
    w, h = img.size
    if min(w, h) > SIDE:
        s = SIDE / min(w, h)
        img = img.resize((round(w * s), round(h * s)), Image.LANCZOS)
    elif min(w, h) < CROP:
        s = CROP / min(w, h)
        img = img.resize((math.ceil(w * s), math.ceil(h * s)), Image.BICUBIC)
    w, h = img.size
    cx, cy = (w - CROP) // 2, (h - CROP) // 2
    if w >= h:
        return [img.crop((x, cy, x + CROP, cy + CROP)) for x in (0, cx, w - CROP)]
    return [img.crop((cx, y, cx + CROP, y + CROP)) for y in (0, cy, h - CROP)]


def main(folder: Path) -> None:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    st = SentenceTransformer(str(MODEL), device=device, config_kwargs={"audio_config": None},
                             model_kwargs={"torch_dtype": torch.bfloat16 if device == "cuda" else torch.float32})
    ip = st[0].processor.image_processor
    ip.max_soft_tokens = ip.image_seq_length = BUDGET
    labels = json.loads((folder / "labels.json").read_text(encoding="utf-8"))
    rows = []
    for name, meta in labels.items():
        try:
            img = Image.open(folder / name)
            with torch.no_grad():
                logits = st.encode([{"image": v} for v in views(img)], batch_size=3, convert_to_tensor=True).float().view(1, 3)
            p = torch.sigmoid(logits.mean(1)).item()
        except Exception as e:  # one unreadable file must not stop the run
            print(f"skip {name}: {type(e).__name__}")
            continue
        rows.append({"name": name, "label": meta["label"], "p_ai": p, "title": meta["title"]})
    (folder / "scores.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    for label in ("ai", "real"):
        v = sorted(r["p_ai"] for r in rows if r["label"] == label)
        if v:
            print(f"{label:5s} n={len(v):3d} min {v[0]:.3f} median {v[len(v) // 2]:.3f} max {v[-1]:.3f}")
    print(f"{'cut':>5s} {'AI caught':>10s} {'real flagged':>13s}")
    for cut in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        ai = [r for r in rows if r["label"] == "ai"]
        re_ = [r for r in rows if r["label"] == "real"]
        print(f"{cut:5.1f} {sum(r['p_ai'] >= cut for r in ai) / max(len(ai), 1):10.0%} {sum(r['p_ai'] >= cut for r in re_) / max(len(re_), 1):13.0%}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
