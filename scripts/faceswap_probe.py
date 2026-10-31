import io, statistics, subprocess, sys
from pathlib import Path
import numpy as np, torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModelForImageClassification

model_id, base = sys.argv[1], Path(sys.argv[2])
proc = AutoImageProcessor.from_pretrained(model_id)
m = AutoModelForImageClassification.from_pretrained(model_id).eval().to("cuda")
print(model_id, m.config.id2label, flush=True)
fake = [i for i, l in m.config.id2label.items() if any(w in l.lower() for w in ("fake", "ai", "synthetic", "deepfake"))]
real = [i for i, l in m.config.id2label.items() if i not in fake]
print("fake idx", fake)

def frames_raw(p, n=8):
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout or 0)
    imgs = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(p), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
        if raw:
            imgs.append(Image.open(io.BytesIO(raw)).convert("RGB"))
    return imgs

import cv2
cas = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
def crop(im):
    g = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2GRAY)
    f = cas.detectMultiScale(g, 1.1, 5, minSize=(60, 60))
    if len(f) == 0:
        return None
    x, y, w, h = max(f, key=lambda r: r[2] * r[3])
    m = int(0.25 * w)
    return im.crop((max(0, x - m), max(0, y - m), min(im.width, x + w + m), min(im.height, y + h + m)))
def frames(p, n=8):
    out = [crop(i) for i in frames_raw(p, n)]
    return [c for c in out if c is not None]
rows = []
for kind in ("real", "fake"):
    for f in sorted((base / kind).rglob("*.mp4")):
        ims = frames(f)
        if not ims:
            continue
        inp = proc(images=ims, return_tensors="pt").to("cuda")
        with torch.no_grad():
            p = torch.softmax(m(**inp).logits.float(), -1).cpu()
        pf = [float(sum(r[i] for i in fake)) for r in p]
        method = f.parent.name if kind == "fake" else "real"
        rows.append((kind, method, statistics.median(pf), max(pf)))
        print(kind, method, f.name[:30], "median %.2f max %.2f" % (rows[-1][2], rows[-1][3]), flush=True)
by = {}
for k, mth, med, mx in rows:
    by.setdefault(mth, []).append((med, mx))
for mth, v in by.items():
    print(f"{mth:28s} n={len(v)} median-of-median {statistics.median(x[0] for x in v):.2f}")
for cut in (0.3, 0.5, 0.7, 0.9):
    r = [x for k, _, x, _ in rows if k == "real"]; f_ = [x for k, _, x, _ in rows if k == "fake"]
    print(f"cut {cut}: fakes caught {sum(x >= cut for x in f_)}/{len(f_)} real flagged {sum(x >= cut for x in r)}/{len(r)}")
