"""Voice notes to text with faster-whisper, so a forwarded voice message goes through the same claim agent as typed text.

The model runs locally from a folder on disk (nothing is sent to a service) and loads on first use. Whisper picks the language itself;
the result carries it with its probability, and a low probability is shown as uncertain rather than trusted. A transcript is only a
reading of the audio: errors in it (names, numbers, dialect) flow into the claim, so the UI shows the transcript before the verdict.
This module reads speech only. It does not tell whether a voice is real or cloned.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import httpx
import numpy as np

from ..config import Config

SAMPLE_RATE = 16000                    # what Whisper expects
BYTES_PER_SAMPLE = 4                   # float32 mono


@dataclass
class Transcript:
    text: str
    language: str
    language_prob: float
    language_uncertain: bool
    duration_s: float
    seconds: float                      # how long transcription took
    model: str


def decode(data: bytes, timeout_s: float) -> np.ndarray:
    """Any audio format to 16 kHz mono float32 with ffmpeg (a plain argument list, no shell). Done here, not by the speech library's own
    decoder, because that one breaks when its PyAV dependency moves on."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise VoiceError("ffmpeg is not installed or not on PATH")
    try:
        out = subprocess.run([exe, "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"],
                             input=data, capture_output=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        raise VoiceError("reading the audio took too long") from None
    if not out.stdout:
        raise VoiceError("could not read the audio: not a recognised audio format")
    return np.frombuffer(out.stdout[: len(out.stdout) // BYTES_PER_SAMPLE * BYTES_PER_SAMPLE], dtype=np.float32)


class VoiceError(ValueError):
    """The audio could not be read or is outside the limits; the message is safe to show."""


class Transcriber:
    def __init__(self, cfg: Config, root: Path):
        self.s = cfg.runtime.voice
        self.path = Path(self.s.model_dir) if Path(self.s.model_dir).is_absolute() else root / self.s.model_dir
        self._model = None
        self._lock = Lock()

    def _load(self):
        if self._model is None:
            if not self.path.is_dir():
                raise VoiceError("the speech model is not installed on this machine (see CLAUDE.md, voice notes)")
            from faster_whisper import WhisperModel          # imported late: it is heavy and the API runs without it
            self._model = WhisperModel(str(self.path), device=self.s.device, compute_type=self.s.compute_type)
        return self._model

    def _via_service(self, audio: np.ndarray) -> dict | None:
        """The GPU service's answer, or None when it is down (the caller then falls back to the CPU model)."""
        try:
            r = httpx.post(f"{self.s.service_url}/transcribe", content=audio.tobytes(), headers={"Content-Type": "application/octet-stream"}, timeout=self.s.service_timeout_s)
        except httpx.HTTPError:
            return None
        return r.json() if r.status_code == httpx.codes.OK else None

    def transcribe(self, data: bytes) -> Transcript:
        if not data or len(data) > self.s.max_bytes:
            raise VoiceError(f"send 1 to {self.s.max_bytes} bytes of audio")
        started = time.time()
        audio = decode(data, self.s.decode_timeout_s)
        if len(audio) / SAMPLE_RATE > self.s.max_seconds:
            raise VoiceError(f"the audio is {len(audio) / SAMPLE_RATE:.0f} s long; the limit is {self.s.max_seconds:.0f} s")
        fast = self._via_service(audio)
        if fast is not None:
            p = float(fast["language_probability"])
            return Transcript(str(fast["text"]), str(fast["language"]), round(p, 3), p < self.s.low_language_prob, float(fast["duration_s"]),
                              round(time.time() - started, 2), f"{self.path.name} (GPU service)")
        with self._lock:                                      # one clip at a time: the model is not shared across threads
            model = self._load()
            try:
                segments, info = model.transcribe(audio, beam_size=self.s.beam_size, vad_filter=self.s.vad_filter)
                text = " ".join(seg.text.strip() for seg in segments).strip()
            except VoiceError:
                raise
            except Exception as exc:                          # undecodable audio: the class only, the text could carry paths
                raise VoiceError(f"could not read the audio ({type(exc).__name__})") from None
        return Transcript(text, info.language, round(float(info.language_probability), 3), info.language_probability < self.s.low_language_prob,
                          round(info.duration, 2), round(time.time() - started, 2), f"{self.path.name} (CPU fallback)")
