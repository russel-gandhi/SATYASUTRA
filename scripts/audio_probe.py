"""Score a folder of real/ and fake/ speech with a Hugging Face audio-classification model and print the cut table (used for docs/calibration/audio_synthetic_*.txt).
Run with the GPU Python:  forensics\venv311\Scripts\python scripts\audio_probe.py <model id or folder> <dir containing real/ and fake/>"""
import statistics, subprocess, sys
from pathlib import Path
import numpy as np, torch
from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

model_id, base = sys.argv[1], Path(sys.argv[2])
fe = AutoFeatureExtractor.from_pretrained(model_id)
m = AutoModelForAudioClassification.from_pretrained(model_id).eval().to("cuda")
print(model_id, m.config.id2label, "sr", fe.sampling_rate, flush=True)

def load(p):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-t", "30", "-ac", "1", "-ar", str(fe.sampling_rate), "-f", "s16le", "-"], capture_output=True).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768

fake_idx = [i for i, l in m.config.id2label.items() if any(w in l.lower() for w in ("fake", "spoof", "synthetic", "ai"))]
print("fake class idx", fake_idx)
res = {"real": [], "fake": []}
for kind in res:
    for f in sorted((base / kind).glob("*")):
        if f.suffix == ".txt":
            continue
        a = load(f)
        if len(a) < fe.sampling_rate:
            continue
        win = fe.sampling_rate * 6
        ps = []
        for s in range(0, max(1, len(a) - win // 2), win):
            x = a[s:s + win]
            if len(x) < fe.sampling_rate:
                continue
            inp = fe(x, sampling_rate=fe.sampling_rate, return_tensors="pt")
            with torch.no_grad():
                p = torch.softmax(m(**{k: v.to("cuda") for k, v in inp.items()}).logits, -1)[0]
            ps.append(float(sum(p[i] for i in fake_idx)))
        res[kind].append((f.name, max(ps), statistics.median(ps)))
        print(kind, f.name, "max_fake %.2f median_fake %.2f" % (max(ps), statistics.median(ps)), flush=True)
for cut in [0.5, 0.7, 0.9, 0.97]:
    for stat, idx in (("max", 1), ("median", 2)):
        tp = sum(r[idx] >= cut for r in res["fake"]); fp = sum(r[idx] >= cut for r in res["real"])
        print(f"cut {cut} {stat}: fakes caught {tp}/{len(res['fake'])} real flagged {fp}/{len(res['real'])}")
