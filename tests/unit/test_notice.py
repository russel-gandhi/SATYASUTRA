"""Notice checker: the model only copies the text; links, wording and the issuer claim are checked by code."""

import io
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from satyasutra.api.app import create_app
from satyasutra.config import load_config
from satyasutra.errors import QuotaExhausted
from satyasutra.llm.gemini import Reply
from satyasutra.notice import NoticeError, check_notice
from satyasutra.sources.index import OfficialIndex, Passage

CFG = load_config()
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
SCAM = {"text": "RBI NOTICE: your account is blocked. Update KYC today at https://rbi-kyc-update.com or pay a fee of 500 and share your OTP.",
        "issuer": "Reserve Bank of India", "urls": ["https://rbi-kyc-update.com"], "phone_numbers": [], "asks_for": ["share OTP", "pay a fee"]}
RELEASE = "RBI keeps the policy repo rate unchanged at 5.5 per cent and the standing deposit facility rate at 5.25 per cent in the October monetary policy statement"


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="PNG")
    return buf.getvalue()


class FakeGemini:
    def __init__(self, data=None, error=None):
        self.data, self.error, self.images = data, error, []

    def generate_json(self, prompt, schema, system=None, images=None):
        self.images = images
        if self.error:
            raise self.error
        return Reply(self.data, "m", None, 0.1, 1)


def index(tmp_path, with_release=False) -> OfficialIndex:
    idx = OfficialIndex(tmp_path / "o.sqlite3")
    if with_release:
        idx.add(Passage("rbi_press", "Reserve Bank of India", 1, "https://www.rbi.org.in/x", "Monetary Policy Statement", RELEASE, "sha1", NOW, NOW))
    return idx


def test_the_image_goes_to_the_model_and_a_lookalike_link_with_scam_wording_is_a_red_flag(tmp_path):
    g = FakeGemini(SCAM)
    r = check_notice(g, index(tmp_path), png(), CFG)
    assert g.images and g.images[0][0] == "image/png"
    levels = {(f["kind"], f["level"]) for f in r.findings}
    assert ("link", "red_flag") in levels and ("wording", "red_flag") in levels and ("official", "red_flag") in levels
    assert "no matching release" in " ".join(f["text"] for f in r.findings) and "does not prove it is fake" in " ".join(f["text"] for f in r.findings)
    assert r.plan["intent"] == "scam_forward"


def test_a_notice_that_matches_a_release_in_the_official_feed_says_so_and_points_to_it(tmp_path):
    text = "Reserve Bank of India: the policy repo rate is unchanged at 5.5 per cent and the standing deposit facility rate is 5.25 per cent"
    r = check_notice(FakeGemini({**SCAM, "text": text, "urls": [], "asks_for": []}), index(tmp_path, True), png(), CFG)
    assert r.official_match and r.official_match["url"] == "https://www.rbi.org.in/x"
    assert any(f["kind"] == "official" and f["level"] == "note" and "Compare the details yourself" in f["text"] for f in r.findings)
    assert not any(f["level"] == "red_flag" for f in r.findings)


def test_an_empty_read_is_said_plainly_and_a_model_outage_or_a_bad_image_is_refused(tmp_path):
    r = check_notice(FakeGemini({"text": "", "issuer": "", "urls": [], "phone_numbers": [], "asks_for": []}), index(tmp_path), png(), CFG)
    assert r.findings[0]["kind"] == "unreadable"
    with pytest.raises(NoticeError, match="nothing was checked"):
        check_notice(FakeGemini(error=QuotaExhausted("gemini_llm", "daily quota used up")), index(tmp_path), png(), CFG)
    with pytest.raises(NoticeError, match="not a readable image"):
        check_notice(FakeGemini(SCAM), index(tmp_path), b"not an image", CFG)


def test_the_route_returns_the_findings_with_a_note_that_it_is_not_a_verdict(tmp_path, monkeypatch):
    from satyasutra.notice import check_notice as real

    monkeypatch.setattr("satyasutra.api.app.refresh_if_stale", lambda cfg, idx: None)
    monkeypatch.setattr("satyasutra.api.app.check_notice", lambda client, idx, data, cfg: real(FakeGemini(SCAM), idx, data, cfg))
    client = TestClient(create_app(CFG))
    body = client.post("/notice/check", content=png()).json()
    assert body["issuer"] == "Reserve Bank of India" and any(f["level"] == "red_flag" for f in body["findings"]) and "not a verdict" in body["note"]
    assert client.post("/notice/check", content=b"junk").status_code == 422
