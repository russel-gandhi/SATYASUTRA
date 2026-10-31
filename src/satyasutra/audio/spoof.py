"""Is this speech synthetic? W2V2-AASIST (Tak et al., Odyssey 2022; MIT; XLS-R 300M plus an AASIST back-end), run from its ONNX file.

The model reads 64,600 samples (about 4 s) of 16 kHz audio and returns a logit where higher means more like a real voice. We score up
to `max_windows` windows spread over the clip and report each. It was trained on ASVspoof 2019 (English, older synthesis methods); the
model card measures 11 percent equal error rate on real-world deepfakes (In-the-Wild) and 16 percent on ASVspoof 5, and near coin-flip
on some modern neural-TTS sets. So a low score is a reason to be suspicious and a high score proves nothing; the label says so, and
nothing here was measured on Indian-language clones. This module returns scores and a cut comparison, never a verdict on a claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

import numpy as np

from ..config import Config
from ..voice.transcribe import SAMPLE_RATE, VoiceError, decode

NOTE = ("Experimental, and no verdict is given. On our own test (docs/calibration/audio_2026-10-31.txt) this model did not separate real recordings from "
        "synthetic speech: it scored modern synthetic voices as more real than real recordings. It was trained on older English synthetic speech and is unreliable on "
        "newer voice-cloning tools and on Indian languages. The scores are shown for transparency only.")


@dataclass
class SpoofResult:
    scores: list[float]                    # bona-fide logit per window: higher is more like a real voice
    cut: float
    seconds: float                         # clip length
    lowest: float = 0.0
    flagged_windows: int = 0
    label: str = "INCONCLUSIVE"
    reasons: list[str] = field(default_factory=list)
    note: str = NOTE


def window_starts(total: int, window: int, count: int) -> list[int]:
    """Start sample of each window, spread evenly over the clip; a clip shorter than a window has one start at 0."""
    if total <= window:
        return [0]
    n = min(count, max(1, total // window))
    last = total - window
    return [round(i * last / (n - 1)) for i in range(n)] if n > 1 else [0]


def pad_fixed(x: np.ndarray, window: int) -> np.ndarray:
    """The model's own windowing: the first `window` samples, tile-repeated when the audio is shorter."""
    if len(x) >= window:
        return x[:window]
    return np.tile(x, int(np.ceil(window / len(x))))[:window]


class SpoofDetector:
    def __init__(self, cfg: Config, root: Path):
        self.cfg = cfg
        self.s = cfg.runtime.audio_spoof
        self.path = Path(self.s.model_path) if Path(self.s.model_path).is_absolute() else root / self.s.model_path
        self._session = None
        self._lock = Lock()

    def available(self) -> bool:
        return self.path.is_file()

    def _load(self):
        if self._session is None:
            if not self.available():
                raise VoiceError("the audio authenticity model is not installed on this machine (see CLAUDE.md)")
            import onnxruntime as ort                                  # imported late: heavy, and the API runs without it
            self._session = ort.InferenceSession(str(self.path), providers=self.s.providers)
        return self._session

    def score(self, audio: np.ndarray) -> list[float]:
        window = self.s.window_samples
        batch = np.stack([pad_fixed(audio[i:i + window], window) for i in window_starts(len(audio), window, self.s.max_windows)]).astype(np.float32)
        with self._lock:
            logits = self._load().run(["logits"], {"wav": batch})[0]
        return [float(v) for v in logits[:, 1]]

    def check(self, data: bytes) -> SpoofResult:
        audio = decode(data, self.cfg.runtime.voice.decode_timeout_s)
        seconds = len(audio) / SAMPLE_RATE
        if seconds < self.s.min_seconds:
            raise VoiceError(f"the clip is {seconds:.1f} s; at least {self.s.min_seconds:.0f} s of speech is needed")
        if seconds > self.cfg.runtime.voice.max_seconds:
            raise VoiceError(f"the audio is {seconds:.0f} s long; the limit is {self.cfg.runtime.voice.max_seconds:.0f} s")
        entry = self.cfg.thresholds.thresholds.get(self.s.threshold)
        scores = self.score(audio)
        result = SpoofResult(scores=[round(v, 3) for v in scores], cut=entry.value if entry else float("nan"), seconds=round(seconds, 2), lowest=round(min(scores), 3))
        if entry is None:
            result.reasons.append("no calibrated cut: the scores are shown but no label is given")
            return result
        result.flagged_windows = sum(v < entry.value for v in scores)
        if result.flagged_windows:
            result.label = "SYNTHETIC_SUSPECTED"
            result.reasons.append(f"{result.flagged_windows} of {len(scores)} windows scored below the cut {entry.value}: the voice may be synthetic")
        else:
            result.reasons.append(f"all {len(scores)} windows scored at or above the cut {entry.value}: no sign of synthetic speech found, which does not prove the voice is real")
        return result
