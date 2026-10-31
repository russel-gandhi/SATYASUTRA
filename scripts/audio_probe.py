"""Score a folder of real/ and fake/ speech with the audio authenticity model and print each file's windows and the separation.
Used to calibrate `audio_bonafide_logit` (docs/calibration/audio_*.txt). Usage: audio_probe.py <dir containing real/ and fake/>"""
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from satyasutra.audio import SpoofDetector       # noqa: E402
from satyasutra.config import load_config        # noqa: E402
from satyasutra.voice.transcribe import decode    # noqa: E402

if __name__ == "__main__":
    base = Path(sys.argv[1])
    det = SpoofDetector(load_config(), ROOT)
    rows = {"real": [], "fake": []}
    for kind in rows:
        for f in sorted((base / kind).glob("*")):
            if f.suffix == ".txt":
                continue
            scores = det.score(decode(f.read_bytes(), 60))
            rows[kind].append((f.name, scores))
            print(f"{kind} {f.name:40s} min {min(scores):7.2f} median {statistics.median(scores):7.2f} windows {len(scores)}")
    for kind, items in rows.items():
        lows = sorted(min(s) for _, s in items)
        print(kind, "n", len(lows), "min-of-windows: lowest", [round(v, 2) for v in lows[:3]], "median", round(statistics.median(lows), 2), "highest", round(lows[-1], 2))
    for cut in [x / 2 for x in range(-8, 9)]:
        tp = sum(min(s) < cut for _, s in rows["fake"]); fp = sum(min(s) < cut for _, s in rows["real"])
        print(f"cut {cut:5.1f}: fakes caught {tp}/{len(rows['fake'])}  real flagged {fp}/{len(rows['real'])}")
