"""Video check: even frame sampling, per-frame scoring, and honest limits. Clips come from ffmpeg's own test pattern."""

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from satyasutra import __version__
from satyasutra.api.app import create_app
from satyasutra.config import ThresholdEntry, load_config
from satyasutra.ledger import SQLiteLedgerStore
from satyasutra.provenance import ImageIndex
from satyasutra.video import check_video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg is not installed")
CONFIG = Path(__file__).resolve().parents[2] / "config"
CUT = 0.7


def clip(tmp_path, seconds=4, name="c.mp4") -> bytes:
    out = tmp_path / name
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=10", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out.read_bytes()


def cfg(**forensics):
    c = load_config(CONFIG)
    t = dict(c.thresholds.thresholds) | {"trufor_manipulation_score": ThresholdEntry(value=CUT)}
    f = c.runtime.forensics.model_copy(update=forensics)
    return c.model_copy(update={"thresholds": c.thresholds.model_copy(update={"thresholds": t}), "runtime": c.runtime.model_copy(update={"forensics": f})})


def scorer(scores):
    calls = iter(scores)
    return lambda c, d: {"available": True, "score": next(calls), "heatmap_png_b64": "AAAA"}


def test_frames_are_sampled_evenly_and_one_flagged_frame_makes_the_video_edited(tmp_path):
    c = cfg(video_frames=4)
    out = check_video(c, clip(tmp_path), ImageIndex(tmp_path / "i.sqlite3"), scorer([0.1, 0.2, 0.9, 0.3]))
    assert [f.at_s for f in out.frames] == [0.5, 1.5, 2.5, 3.5] and [f.flagged for f in out.frames] == [False, False, True, False]
    assert out.label == "EDITED" and out.duration_s == pytest.approx(4, abs=0.2) and out.forensics_available
    text = " ".join(out.reasons)
    assert "1 of 4 frames look changed" in text and "2.5 s" in text and out.headline == "Some frames look edited"
    assert all(f.thumb_jpeg_b64 for f in out.frames)


def test_no_flagged_frame_is_inconclusive_and_the_limits_are_always_stated(tmp_path):
    out = check_video(cfg(video_frames=3), clip(tmp_path), ImageIndex(tmp_path / "i.sqlite3"), scorer([0.1, 0.2, 0.3]))
    text = " ".join(out.reasons)
    assert out.label == "INCONCLUSIVE" and out.headline == "No sign of editing or AI"
    assert "authentic" not in (out.headline + text).lower() and "deepfake" not in (out.headline + text).lower()   # we do not detect face swaps, so we never use the word


def test_an_unreachable_forensics_service_is_reported_not_guessed(tmp_path):
    down = lambda c, d: {"available": False, "reason": "forensics service unreachable (ConnectError)"}
    out = check_video(cfg(video_frames=2), clip(tmp_path), ImageIndex(tmp_path / "i.sqlite3"), down)
    assert out.label == "INCONCLUSIVE" and not out.forensics_available and "unreachable" in " ".join(out.reasons)
    assert all(f.score is None and f.thumb_jpeg_b64 for f in out.frames)


def test_not_a_video_and_too_long_are_refused(tmp_path):
    index = ImageIndex(tmp_path / "i.sqlite3")
    with pytest.raises(ValueError, match="not a readable video"):
        check_video(cfg(), b"this is not a video", index, scorer([]))
    with pytest.raises(ValueError, match="limit is 2 s"):
        check_video(cfg(max_video_s=2), clip(tmp_path, seconds=4), index, scorer([]))


def test_route_returns_frames_with_heatmaps_and_rejects_bad_bodies(tmp_path):
    data = clip(tmp_path, seconds=2)
    c = cfg(video_frames=2)
    app = create_app(c, SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__), analyse_image=scorer([0.9, 0.1]), image_index=ImageIndex(tmp_path / "i.sqlite3"))
    client = TestClient(app)
    body = client.post("/video/check", content=data).json()
    assert body["label"] == "EDITED" and len(body["frames"]) == 2 and body["frames"][0]["heatmap_png_b64"] == "AAAA" and body["frames"][0]["flagged"] is True
    assert client.post("/video/check", content=b"").status_code == 422
    assert client.post("/video/check", content=b"nope").status_code == 422
    assert client.post("/video/check", content=b"0" * (c.runtime.forensics.max_video_bytes + 1)).status_code == 422


def test_several_frames_over_the_ai_cut_make_the_video_synthetic_suspected_but_one_frame_does_not(tmp_path):
    ai_cut = load_config(CONFIG).thresholds.thresholds[load_config(CONFIG).runtime.forensics.ai_threshold].value
    def ai_scorer(ai_scores):
        it = iter(ai_scores)
        return lambda c, d: {"available": True, "score": 0.1, "ai_score": next(it), "heatmap_png_b64": "AAAA"}
    many = check_video(cfg(video_frames=4), clip(tmp_path), ImageIndex(tmp_path / "i.sqlite3"), ai_scorer([ai_cut + 0.1, ai_cut + 0.2, ai_cut + 0.3, 0.0]))
    assert many.label == "SYNTHETIC_SUSPECTED" and "3 of 4 frames look AI-made" in " ".join(many.reasons) and many.headline == "Mostly AI-made"
    one = check_video(cfg(video_frames=4), clip(tmp_path), ImageIndex(tmp_path / "j.sqlite3"), ai_scorer([ai_cut + 0.1, 0.0, 0.0, 0.0]))
    assert one.label == "INCONCLUSIVE" and one.headline == "No sign of editing or AI" and one.frames[0].ai_flagged
