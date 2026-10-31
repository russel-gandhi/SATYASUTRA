"""The media check: provenance matches and the forensic read are separate signals, and "found nothing" is never "authentic"."""

import io
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from satyasutra import __version__
from satyasutra.api.app import create_app
from satyasutra.config import ThresholdEntry, load_config
from satyasutra.ledger import SQLiteLedgerStore
from satyasutra.media import check_media, forensic_scores
from satyasutra.provenance import ImageIndex, fingerprint
from satyasutra.provenance.index import Source

CONFIG = Path(__file__).resolve().parents[2] / "config"
CUT = 0.5


def png(seed=1, size=(96, 96)) -> bytes:
    rng = np.random.default_rng(seed)
    out = io.BytesIO()
    Image.fromarray(rng.integers(0, 255, (*size, 3), dtype=np.uint8)).save(out, format="PNG")
    return out.getvalue()


def cfg(with_cut=True):
    c = load_config(CONFIG)
    t = dict(c.thresholds.thresholds)
    t.pop("trufor_manipulation_score", None)
    if with_cut:
        t["trufor_manipulation_score"] = ThresholdEntry(value=CUT)
    return c.model_copy(update={"thresholds": c.thresholds.model_copy(update={"thresholds": t})})


def index(tmp_path, data=None):
    idx = ImageIndex(tmp_path / "i.sqlite3")
    if data:
        idx.add(fingerprint(data), Source(publisher="Alt News", lang="en", title="Old photo, new claim", article_url="https://x.org/a", image_url="https://x.org/i.png",
                                          reviewed_at=datetime(2026, 1, 1, tzinfo=timezone.utc), labels="misleading"))
    return idx


def scores(score, available=True):
    return lambda c, d: {"available": True, "score": score, "width": 96, "height": 96, "heatmap_png_b64": "AAAA"} if available else {"available": False, "reason": "forensics service unreachable (ConnectError)"}


def test_a_score_at_or_above_the_cut_is_edited_and_below_is_inconclusive_never_authentic(tmp_path):
    data = png()
    high = check_media(cfg(), data, index(tmp_path), scores(0.9))
    low = check_media(cfg(), data, index(tmp_path), scores(0.1))
    assert high.label == "EDITED" and "heatmap" in " ".join(high.reasons)
    assert low.label == "INCONCLUSIVE" and "does not prove the image is genuine" in " ".join(low.reasons)
    assert "AI-generated" in " ".join(low.reasons)                                  # the scope limit is always stated


def test_without_a_calibrated_cut_the_score_is_shown_but_no_label_is_given(tmp_path):
    out = check_media(cfg(with_cut=False), png(), index(tmp_path), scores(0.99))
    assert out.label == "INCONCLUSIVE" and out.forensics["score"] == 0.99 and "no calibrated" in " ".join(out.reasons)


def test_an_unreachable_forensics_service_is_reported_and_provenance_still_answers(tmp_path):
    data = png()
    out = check_media(cfg(), data, index(tmp_path, data), scores(None, available=False))
    assert out.label == "INCONCLUSIVE" and out.forensics["available"] is False and "unreachable" in " ".join(out.reasons)
    assert len(out.matches) == 1 and out.matches[0]["publisher"] == "Alt News" and out.matches[0]["distance"] == 0


def test_a_debunked_image_match_is_a_lead_not_a_verdict(tmp_path):
    data = png()
    out = check_media(cfg(), data, index(tmp_path, data), scores(0.1))
    text = " ".join(out.reasons)
    assert out.matches and "not a verdict on any claim" in text and out.label == "INCONCLUSIVE"
    none = check_media(cfg(), png(seed=2), index(tmp_path, data), scores(0.1))
    assert none.matches == [] and "says nothing about whether it is authentic" in " ".join(none.reasons)


def test_not_an_image_is_refused(tmp_path):
    with pytest.raises(ValueError):
        check_media(cfg(), b"not an image at all", index(tmp_path), scores(0.1))


def test_the_forensic_client_reports_http_errors_and_connection_failures_without_leaking_text():
    def refuse(request):
        return httpx.Response(422, json={"detail": "could not analyse the image (UnidentifiedImageError)"})

    def boom(request):
        raise httpx.ConnectError("connect failed at http://127.0.0.1:8100/analyze?key=SECRET")

    c = cfg()
    refused = forensic_scores(c, b"x", httpx.Client(transport=httpx.MockTransport(refuse)))
    down = forensic_scores(c, b"x", httpx.Client(transport=httpx.MockTransport(boom)))
    assert refused == {"available": False, "reason": "forensics service refused the image (HTTP 422)"}
    assert down["available"] is False and "ConnectError" in down["reason"] and "SECRET" not in repr(down)


def test_route_takes_raw_bytes_returns_the_heatmap_separately_and_rejects_bad_bodies(tmp_path):
    data = png()
    app = create_app(cfg(), SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__), analyse_image=scores(0.9), image_index=index(tmp_path, data))
    client = TestClient(app)
    ok = client.post("/media/check", content=data)
    body = ok.json()
    assert ok.status_code == 200 and body["label"] == "EDITED" and body["heatmap_png_b64"] == "AAAA" and "heatmap_png_b64" not in body["forensics"]
    assert body["matches"][0]["article_url"] == "https://x.org/a" and body["width"] == 96
    assert client.post("/media/check", content=b"").status_code == 422
    assert client.post("/media/check", content=b"garbage").status_code == 422
    too_big = cfg().runtime.forensics.max_upload_bytes + 1
    assert client.post("/media/check", content=b"0" * too_big).status_code == 422


def scores_ai(ai, edit=0.1):
    return lambda c, d: {"available": True, "score": edit, "ai_score": ai, "ai_error": None, "heatmap_png_b64": "AAAA"}


def test_ai_score_at_or_above_its_cut_is_synthetic_and_below_is_inconclusive_with_the_limits_stated(tmp_path):
    c = cfg()
    cut = c.thresholds.thresholds[c.runtime.forensics.ai_threshold].value
    high = check_media(c, png(), index(tmp_path), scores_ai(min(cut + 0.4, 1.0)))
    low = check_media(c, png(), index(tmp_path), scores_ai(max(cut - 0.4, 0.0)))
    assert high.label == "SYNTHETIC" and "looks AI-generated" in " ".join(high.reasons)
    assert low.label == "INCONCLUSIVE" and "does not prove it is a real photo" in " ".join(low.reasons) and "evasive generators" in " ".join(low.reasons)


def test_edited_and_synthetic_are_both_reported_and_a_missing_detector_is_named(tmp_path):
    both = check_media(cfg(), png(), index(tmp_path), scores_ai(0.99, edit=0.9))
    text = " ".join(both.reasons)
    assert both.label == "SYNTHETIC" and "manipulation score" in text and "AI-generated score" in text
    gone = lambda c, d: {"available": True, "score": 0.1, "ai_score": None, "ai_error": "Smaug weights are not installed", "heatmap_png_b64": "AAAA"}
    out = check_media(cfg(), png(), index(tmp_path), gone)
    assert out.label == "INCONCLUSIVE" and "AI-image detector unavailable: Smaug weights are not installed" in " ".join(out.reasons)
