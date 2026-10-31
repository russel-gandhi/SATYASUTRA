"""Voice notes: decoding, the limits, the GPU-service-first path with its CPU fallback, and the route."""

import io
import json
import shutil
import struct
import wave

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from satyasutra.api.app import create_app
from satyasutra.config import load_config
from satyasutra.voice import Transcriber, Transcript, VoiceError
from satyasutra.voice.transcribe import decode


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed (CI installs it, so these run there)")


def wav(seconds: float, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(seconds * rate))
    return buf.getvalue()


@needs_ffmpeg
def test_decode_reads_a_wav_to_16k_float_samples_and_refuses_junk():
    audio = decode(wav(0.5, rate=8000), 20)
    assert audio.dtype == np.float32 and abs(len(audio) - 8000) < 200          # resampled from 8 kHz to 16 kHz
    with pytest.raises(VoiceError):
        decode(b"this is not audio", 20)


@needs_ffmpeg
def test_limits_are_enforced_before_any_model_runs(tmp_path):
    cfg = load_config()
    t = Transcriber(cfg, tmp_path)
    with pytest.raises(VoiceError):
        t.transcribe(b"")
    with pytest.raises(VoiceError, match="limit"):
        t.transcribe(wav(cfg.runtime.voice.max_seconds + 2))


@needs_ffmpeg
def test_the_service_answer_is_used_and_a_low_language_probability_is_flagged(tmp_path, monkeypatch):
    cfg = load_config()
    t = Transcriber(cfg, tmp_path)
    reply = {"text": "namaste", "language": "hi", "language_probability": cfg.runtime.voice.low_language_prob / 2, "duration_s": 1.0}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, content=json.dumps(reply).encode(), request=httpx.Request("POST", "http://x")))
    got = t.transcribe(wav(1.0))
    assert got.text == "namaste" and got.language == "hi" and got.language_uncertain and "GPU service" in got.model


def test_a_down_service_falls_back_to_the_local_model_and_a_missing_model_is_said_plainly(tmp_path, monkeypatch):
    def down(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "post", down)
    t = Transcriber(load_config(), tmp_path)                                    # tmp_path holds no model folder
    with pytest.raises(VoiceError, match="not installed"):
        t.transcribe(wav(1.0))


def test_the_route_returns_the_transcript_with_its_caveat_and_maps_errors_to_422():
    seen = {}

    def fake(data: bytes) -> Transcript:
        seen["n"] = len(data)
        if data == b"bad":
            raise VoiceError("could not read the audio")
        return Transcript("rbi will auction bonds", "en", 0.99, False, 3.0, 0.4, "fake")

    client = TestClient(create_app(load_config(), transcribe=fake))
    body = client.post("/voice/transcribe", content=b"abcdef").json()
    assert body["text"] == "rbi will auction bonds" and body["language"] == "en" and "not tell whether the voice is real or cloned" in body["note"]
    assert seen["n"] == 6
    r = client.post("/voice/transcribe", content=b"bad")
    assert r.status_code == 422 and "could not read" in r.json()["detail"]
