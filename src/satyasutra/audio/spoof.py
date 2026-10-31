"""Is this speech synthetic? A wav2vec2 classifier (garystafford/wav2vec2-deepfake-voice-detector, Apache 2.0) fine-tuned on speech from modern
text-to-speech and voice-cloning services, run in the GPU service (`POST /voiceclone`).

The clip is decoded here (ffmpeg, 16 kHz mono), the service scores consecutive 6 s windows and returns the probability of "fake" for each, and the
label comes from the median window against a cut in thresholds.yaml. Only two outcomes exist: SYNTHETIC_SUSPECTED, or INCONCLUSIVE, which never means
"real voice". The cut was measured on a small, weak set (see docs/calibration/audio_synthetic_2026-10-31.txt): old noisy recordings as the real class,
Gemini and Windows speech synthesis as the fake class, no cloned voices of real people and no Indian-language clones other than Gemini Hindi. The
earlier model (W2V2-AASIST) failed the same test and was removed from use.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import httpx
import numpy as np

from ..config import Config
from ..voice.transcribe import SAMPLE_RATE, VoiceError, decode

NOTE = ("Experimental. A model trained on speech from modern voice-cloning services scored our test clips (docs/calibration/audio_synthetic_2026-10-31.txt), "
        "but the test was small and had no cloned voices of real people. A low score proves nothing: the model can miss new tools, phone-call audio and noisy "
        "recordings, and a real voice can be flagged. Treat a flag as a reason to ask where the recording came from, not as proof.")


@dataclass
class SpoofResult:
    scores: list[float]                    # probability that each window is synthetic, 0..1
    cut: float
    seconds: float                         # clip length
    lowest: float = 0.0                    # kept for the route's shape: here the median window score
    flagged_windows: int = 0
    label: str = "INCONCLUSIVE"
    reasons: list[str] = field(default_factory=list)
    note: str = NOTE


class SpoofDetector:
    def __init__(self, cfg: Config, root=None):
        self.cfg = cfg
        self.s = cfg.runtime.audio_spoof

    def score(self, audio: np.ndarray) -> list[float]:
        try:
            r = httpx.post(f"{self.s.service_url}/voiceclone", content=audio.astype(np.float32).tobytes(),
                           headers={"Content-Type": "application/octet-stream"}, timeout=self.s.service_timeout_s)
            body = r.json() if r.status_code == httpx.codes.OK else None
        except (httpx.HTTPError, ValueError):
            body = None
        if not body or not body.get("fake_probs"):
            raise VoiceError("the voice-check service is not available (start forensics/service.py and install the model, see CLAUDE.md)")
        return [float(v) for v in body["fake_probs"]]

    def check(self, data: bytes) -> SpoofResult:
        audio = decode(data, self.cfg.runtime.voice.decode_timeout_s)
        seconds = len(audio) / SAMPLE_RATE
        if seconds < self.s.min_seconds:
            raise VoiceError(f"the clip is {seconds:.1f} s; at least {self.s.min_seconds:.0f} s of speech is needed")
        if seconds > self.cfg.runtime.voice.max_seconds:
            raise VoiceError(f"the audio is {seconds:.0f} s long; the limit is {self.cfg.runtime.voice.max_seconds:.0f} s")
        entry = self.cfg.thresholds.thresholds.get(self.s.threshold)
        scores = self.score(audio)
        middle = statistics.median(scores)
        result = SpoofResult(scores=[round(v, 3) for v in scores], cut=entry.value if entry else float("nan"), seconds=round(seconds, 2), lowest=round(middle, 3))
        if entry is None:
            result.reasons.append("no calibrated cut: the scores are shown but no label is given")
            return result
        result.flagged_windows = sum(v >= entry.value for v in scores)
        if middle >= entry.value:
            result.label = "SYNTHETIC_SUSPECTED"
            result.reasons.append(f"the median of {len(scores)} windows scored {middle:.2f} for synthetic speech, at or above the cut {entry.value}: the voice may be synthetic or cloned")
        else:
            result.reasons.append(f"the median of {len(scores)} windows scored {middle:.2f}, below the cut {entry.value}: no sign of synthetic speech found, which does not prove the voice is real")
        return result
