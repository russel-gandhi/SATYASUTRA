"""Audio authenticity: windowing, the no-cut behaviour (no label without a calibrated cut), and the route."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from satyasutra.api.app import create_app
from satyasutra.audio import SpoofDetector, SpoofResult, window_starts
from satyasutra.audio.spoof import pad_fixed
from satyasutra.config import load_config
from satyasutra.voice import VoiceError


def test_windows_are_spread_evenly_and_a_short_clip_gets_one():
    assert window_starts(100, 200, 6) == [0]
    assert window_starts(400, 100, 3) == [0, 150, 300]
    starts = window_starts(1000, 100, 6)
    assert starts[0] == 0 and starts[-1] == 900 and len(starts) == 6


def test_short_audio_is_tiled_to_the_models_fixed_window():
    x = np.arange(4, dtype=np.float32)
    assert list(pad_fixed(x, 10)) == [0, 1, 2, 3, 0, 1, 2, 3, 0, 1] and len(pad_fixed(np.zeros(50, np.float32), 20)) == 20


def test_without_a_calibrated_cut_the_scores_are_shown_and_no_label_is_given(tmp_path, monkeypatch):
    cfg = load_config()
    det = SpoofDetector(cfg, tmp_path)
    monkeypatch.setattr(det, "score", lambda audio: [-5.0, 2.0])
    monkeypatch.setattr("satyasutra.audio.spoof.decode", lambda data, t: np.zeros(16000 * 5, np.float32))
    r = det.check(b"x")
    assert r.label == "INCONCLUSIVE" and r.scores == [-5.0, 2.0] and "no calibrated cut" in r.reasons[0] and "did not separate" in r.note


def test_a_missing_model_and_a_too_short_clip_are_said_plainly(tmp_path, monkeypatch):
    det = SpoofDetector(load_config(), tmp_path)
    with pytest.raises(VoiceError, match="not installed"):
        det.score(np.zeros(100, np.float32))
    monkeypatch.setattr("satyasutra.audio.spoof.decode", lambda data, t: np.zeros(16000, np.float32))
    with pytest.raises(VoiceError, match="at least"):
        det.check(b"x")


def test_the_route_returns_scores_and_maps_errors_to_422():
    def fake(data: bytes) -> SpoofResult:
        if data == b"bad":
            raise VoiceError("could not read the audio")
        return SpoofResult(scores=[0.5], cut=float("nan"), seconds=4.0, lowest=0.5, reasons=["no calibrated cut"])
    client = TestClient(create_app(load_config(), audio_check_fn=fake))
    body = client.post("/audio/check", content=b"abc").json()
    assert body["label"] == "INCONCLUSIVE" and body["cut"] is None and body["scores"] == [0.5]
    assert client.post("/audio/check", content=b"bad").status_code == 422
