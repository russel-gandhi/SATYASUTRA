"""Synthetic-speech check: the label rule (median window against the calibrated cut), the unavailable service, and the route."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from satyasutra.api.app import create_app
from satyasutra.audio import SpoofDetector, SpoofResult
from satyasutra.config import load_config
from satyasutra.voice import VoiceError


def detector(monkeypatch, scores, seconds=10):
    det = SpoofDetector(load_config())
    monkeypatch.setattr(det, "score", lambda audio: scores)
    monkeypatch.setattr("satyasutra.audio.spoof.decode", lambda data, t: np.zeros(int(16000 * seconds), np.float32))
    return det


def test_a_median_window_at_or_above_the_cut_is_suspected_synthetic(monkeypatch):
    cut = load_config().thresholds.thresholds["audio_synthetic_probability"].value
    r = detector(monkeypatch, [0.99, 0.97, 0.2]).check(b"x")
    assert r.label == "SYNTHETIC_SUSPECTED" and r.cut == cut and r.flagged_windows == 2 and "may be synthetic" in r.reasons[0]


def test_below_the_cut_is_inconclusive_and_never_says_the_voice_is_real(monkeypatch):
    r = detector(monkeypatch, [0.99, 0.1, 0.05]).check(b"x")
    assert r.label == "INCONCLUSIVE" and "does not prove the voice is real" in r.reasons[0] and r.flagged_windows == 1


def test_one_loud_window_does_not_decide_the_clip(monkeypatch):
    assert detector(monkeypatch, [0.1, 0.99, 0.1, 0.2]).check(b"x").label == "INCONCLUSIVE"


def test_the_note_says_what_the_test_did_not_cover():
    r = SpoofResult(scores=[0.5], cut=0.9, seconds=4.0)
    assert "small" in r.note and "cloned voices of real people" in r.note


def test_too_short_and_too_long_clips_and_a_missing_service_are_said_plainly(monkeypatch):
    monkeypatch.setattr("satyasutra.audio.spoof.decode", lambda data, t: np.zeros(16000, np.float32))
    with pytest.raises(VoiceError, match="at least"):
        SpoofDetector(load_config()).check(b"x")
    cfg = load_config()
    monkeypatch.setattr("satyasutra.audio.spoof.decode", lambda data, t: np.zeros(int(16000 * (cfg.runtime.voice.max_seconds + 5)), np.float32))
    with pytest.raises(VoiceError, match="limit"):
        SpoofDetector(cfg).check(b"x")
    cfg.runtime.audio_spoof.service_url = "http://127.0.0.1:1"          # nothing listens there
    with pytest.raises(VoiceError, match="not available"):
        SpoofDetector(cfg).score(np.zeros(16000 * 5, np.float32))


def test_the_route_returns_scores_and_maps_errors_to_422():
    def fake(data: bytes) -> SpoofResult:
        if data == b"bad":
            raise VoiceError("could not read the audio")
        return SpoofResult(scores=[0.5], cut=float("nan"), seconds=4.0, lowest=0.5, reasons=["no calibrated cut"])
    client = TestClient(create_app(load_config(), audio_check_fn=fake))
    body = client.post("/audio/check", content=b"abc").json()
    assert body["label"] == "INCONCLUSIVE" and body["cut"] is None and body["scores"] == [0.5]
    assert client.post("/audio/check", content=b"bad").status_code == 422
