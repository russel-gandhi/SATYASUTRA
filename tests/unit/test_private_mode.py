"""Private mode (Raksha RK1, RK2): a private request writes no record, text about private images is neither checked nor stored, and the bot
never downloads a file once a chat is in help mode."""

import io
import json
import sqlite3
from contextlib import closing

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from satyasutra.api.app import create_app
from satyasutra.bot import TelegramBot
from satyasutra.config import load_config
from satyasutra.voice import Transcript

CFG = load_config()
HEADER = {CFG.runtime.telegram.private_header: "1"}
INTIMATE = "he is threatening to leak my private photos unless I pay"


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (120, 80, 200)).save(buf, "PNG")
    return buf.getvalue()


def make(tmp_path, transcript="vaccines contain microchips"):
    cfg = CFG.model_copy(deep=True)
    cfg.runtime.report.db_path = str(tmp_path / "records.sqlite3")
    cfg.runtime.provenance.db_path = str(tmp_path / "prov.sqlite3")
    fake_scores = lambda c, d: {"available": True, "score": 0.1, "width": 64, "height": 64, "heatmap_png_b64": "AAAA", "ai_score": 0.0}
    app = create_app(cfg, transcribe=lambda data: Transcript(transcript, "en", 0.99, False, 3.0, 0.4, "fake"), analyse_image=fake_scores)
    return TestClient(app), cfg


def rows(cfg) -> int:
    with closing(sqlite3.connect(cfg.runtime.report.db_path)) as db:
        try:
            return db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        except sqlite3.OperationalError:                         # the table does not exist: nothing was ever written
            return 0


def test_without_the_header_an_image_is_kept_so_the_zero_below_means_something(tmp_path):
    client, cfg = make(tmp_path)
    assert client.post("/media/check", content=png()).json()["record_id"]
    assert rows(cfg) == 1


def test_a_private_photo_and_a_private_voice_note_write_zero_rows(tmp_path):
    client, cfg = make(tmp_path)
    assert client.post("/media/check", content=png(), headers=HEADER).json()["record_id"] is None
    assert client.post("/voice/transcribe", content=b"audio", headers=HEADER).json()["text"]
    assert rows(cfg) == 0


def test_a_voice_note_that_talks_about_private_images_is_never_stored_even_without_the_header(tmp_path):
    client, cfg = make(tmp_path, transcript=INTIMATE)
    out = client.post("/voice/transcribe", content=b"audio").json()
    assert out["record_id"] is None and rows(cfg) == 0


def test_text_about_private_images_is_not_checked_and_so_never_reaches_the_ledger(tmp_path):
    client, cfg = make(tmp_path)
    r = client.post("/verify", json={"text": INTIMATE})
    assert r.status_code == 409 and "nothing was checked or stored" in r.json()["detail"]
    assert client.get("/agent/stream", params={"text": INTIMATE}).status_code == 409
    assert rows(cfg) == 0


# --- the bot -------------------------------------------------------------------------------------------------------------------------------


def bot_with(seen: list):
    def local(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get(CFG.runtime.telegram.private_header)))
        if request.url.path == "/act/plan":
            body = json.loads(request.content)
            intent = "intimate_images" if "private photos" in body["text"] else "none"
            return httpx.Response(200, json={"intent": intent, "routes": [{"title": "Call 112", "contact": "112"}] if intent != "none" else []})
        if request.url.path == "/verify":
            return httpx.Response(200, json={"claims": []})
        return httpx.Response(200, json={"label": "INCONCLUSIVE", "reasons": [], "matches": []})

    downloads: list = []

    def telegram(request: httpx.Request) -> httpx.Response:
        downloads.append(request.url.path)
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"result": {"file_path": "x"}})
        return httpx.Response(200, content=b"pixels")

    b = TelegramBot(CFG, token="t0k")
    b.local = httpx.Client(base_url="http://local", transport=httpx.MockTransport(local), headers=HEADER)
    b.api = httpx.Client(base_url="http://tg", transport=httpx.MockTransport(telegram))
    return b, downloads


def test_help_mode_starts_from_the_message_and_then_a_photo_is_refused_before_any_download():
    seen: list = []
    b, downloads = bot_with(seen)
    reply = b.handle({"chat": {"id": 7}, "text": INTIMATE})
    assert "Call 112" in reply and "/verify" not in [p for p, _ in seen]               # guidance, and the claim path never ran
    out = b.handle({"chat": {"id": 7}, "photo": [{"file_id": "p1"}]})
    assert out == CFG.runtime.telegram.messages["help_images_refused"]
    assert downloads == [] and "/media/check" not in [p for p, _ in seen]


def test_outside_help_mode_a_photo_still_works_and_every_call_carries_the_private_header():
    seen: list = []
    b, downloads = bot_with(seen)
    b.handle({"chat": {"id": 8}, "photo": [{"file_id": "p1"}]})
    assert downloads and ("/media/check", "1") in seen
    assert all(h == "1" for _, h in seen)


def test_forget_leaves_help_mode_and_a_photo_caption_about_private_images_is_not_downloaded():
    seen: list = []
    b, downloads = bot_with(seen)
    b.handle({"chat": {"id": 9}, "text": "/help_me"})
    assert b.in_help(9)
    assert b.handle({"chat": {"id": 9}, "text": "/forget"}) == CFG.runtime.telegram.messages["forgot"] and not b.in_help(9)
    b.handle({"chat": {"id": 10}, "photo": [{"file_id": "p"}], "caption": INTIMATE})
    assert downloads == [] and b.in_help(10)
