"""The Telegram front door: reply formatting and routing of text, voice and photo, with Telegram and our API as local fakes."""

import json

import httpx

from satyasutra.bot import TelegramBot, format_claims, format_media
from satyasutra.config import load_config

CFG = load_config()
S = CFG.runtime.telegram

CLAIM = {"text": "Vaccines contain microchips.", "label": "REFUTED", "confidence_band": "medium", "ledger_seq": 7,
         "reasons": ["rated 'False' -> refutes", "rated 'False' -> refutes", "trusted source contradicts the claim"],
         "citations": [{"publisher": "FactCheck.org", "url": "https://example.org/a"}]}


def test_claims_are_formatted_with_plain_labels_deduplicated_reasons_and_the_source():
    out = format_claims({"claims": [CLAIM]}, S.labels, "none")
    assert S.labels["REFUTED"] in out and "medium confidence" in out and out.count("rated 'False'") == 1 and "FactCheck.org: https://example.org/a" in out
    assert format_claims({"claims": []}, S.labels, "none") == "none"


def test_media_result_never_calls_an_inconclusive_image_genuine():
    out = format_media({"label": "INCONCLUSIVE", "reasons": ["no match"], "matches": [{"url": "https://example.org/f"}]}, S.labels)
    assert "does not mean the image is genuine" in out and "Seen in a fact-check: https://example.org/f" in out


def bot(handler_local, handler_tg) -> TelegramBot:
    b = TelegramBot(CFG, token="t0k")
    b.local = httpx.Client(base_url="http://local", transport=httpx.MockTransport(handler_local))
    b.api = httpx.Client(base_url="http://tg", transport=httpx.MockTransport(handler_tg))
    return b


def local(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/verify":
        return httpx.Response(200, json={"claims": [CLAIM]})
    if request.url.path == "/act/plan":
        return httpx.Response(200, json={"intent": "false_claim", "why": "the claim was checked and refuted", "notice": "We do not file anything.",
                                         "routes": [{"title": "Share the correction", "contact": ""}]})
    if request.url.path == "/voice/transcribe":
        return httpx.Response(200, json={"text": "vaccines contain microchips", "language": "en", "language_uncertain": True})
    if request.url.path == "/media/check":
        return httpx.Response(422 if request.content == b"junk" else 200, json={"label": "INCONCLUSIVE", "reasons": [], "matches": []})
    return httpx.Response(404)


def telegram(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/getFile"):
        return httpx.Response(200, json={"result": {"file_path": json.loads(request.content)["file_id"]}})
    return httpx.Response(200, content=b"junk" if request.url.path.endswith("/bad") else b"pixels")


def test_text_goes_to_verify_then_adds_next_steps_for_a_refuted_claim():
    out = bot(local, telegram).handle({"text": "Vaccines contain microchips"})
    assert S.labels["REFUTED"] in out and "Next steps" in out and "Share the correction" in out and "We do not file anything." in out


def test_commands_get_the_welcome_and_unsupported_types_are_said_plainly():
    b = bot(local, telegram)
    assert b.handle({"text": "/start"}) == S.messages["start"]
    assert b.handle({"sticker": {}}) == S.messages["unsupported"]


def test_a_voice_note_is_transcribed_shown_flagged_if_uncertain_and_then_checked():
    out = bot(local, telegram).handle({"voice": {"file_id": "f"}})
    assert "Voice note (en): vaccines contain microchips" in out and S.messages["uncertain"] in out and S.labels["REFUTED"] in out


def test_a_photo_is_checked_and_an_unreadable_one_gets_the_plain_message():
    b = bot(local, telegram)
    assert S.labels["INCONCLUSIVE"] in b.handle({"photo": [{"file_id": "a"}, {"file_id": "b"}]})
    assert b.handle({"photo": [{"file_id": "bad"}]}) == S.messages["image_failed"]          # the fake Telegram serves b"junk" for it -> 422
