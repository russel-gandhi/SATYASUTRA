"""The Telegram front door: reply formatting and routing of text, voice and photo, with Telegram and our API as local fakes."""

import json

import httpx

from satyasutra.bot import TelegramBot, format_claims, format_media
from satyasutra.config import load_config

CFG = load_config()
S = CFG.runtime.telegram

M = S.messages
CLAIM = {"text": "Vaccines contain microchips.", "label": "REFUTED", "confidence_band": "medium", "ledger_seq": 7, "flags": [],
         "reasons": ["rated 'False' -> refutes"],
         "citations": [{"publisher": "FactCheck.org", "rating": "False", "url": "https://example.org/a"},
                       {"publisher": "FactCheck.org", "rating": "False", "url": "https://example.org/b"}]}


def test_a_refuted_claim_is_said_plainly_with_who_checked_it_the_link_and_no_jargon():
    out = format_claims({"claims": [CLAIM]}, S.labels, M)
    assert 'This is false. FactCheck.org checked this very claim and rated it "False".' in out
    assert "Read it here: https://example.org/a" in out and "1 other review(s) say the same." in out
    assert "confidence" not in out and "->" not in out and "no trusted" not in out
    assert format_claims({"claims": []}, S.labels, M) == M["none_found"]
    assert M["conflict"] in format_claims({"claims": [dict(CLAIM, flags=["CONFLICT"])]}, S.labels, M)


def test_media_result_never_calls_an_inconclusive_image_genuine():
    out = format_media({"label": "INCONCLUSIVE", "reasons": [], "matches": [{"url": "https://example.org/f"}]}, S.labels, M)
    assert "doesn't make the photo real" in out and "appeared in a fact-check before: https://example.org/f" in out


def bot(handler_local, handler_tg) -> TelegramBot:
    b = TelegramBot(CFG, token="t0k")
    b.local = httpx.Client(base_url="http://local", transport=httpx.MockTransport(handler_local))
    b.api = httpx.Client(base_url="http://tg", transport=httpx.MockTransport(handler_tg))
    return b


def local(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/verify":
        return httpx.Response(200, json={"claims": [CLAIM]})
    if request.url.path == "/news/check":
        return httpx.Response(200, json={"status": "not_found", "outlets_checked": 8, "outlet_names": ["The Hindu", "NDTV"], "hits": []})
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
    assert "This is false." in out and M["next_steps"] in out and "Share the correction" in out and M["act_notice"] in out


def test_commands_get_the_welcome_and_unsupported_types_are_said_plainly():
    b = bot(local, telegram)
    assert b.handle({"text": "/start"}) == S.messages["start"]
    assert b.handle({"sticker": {}}) == S.messages["unsupported"]


def test_a_voice_note_is_transcribed_shown_flagged_if_uncertain_and_then_checked():
    out = bot(local, telegram).handle({"voice": {"file_id": "f"}})
    assert "You said (en): vaccines contain microchips" in out and M["uncertain"] in out and "This is false." in out


def test_a_photo_is_checked_and_an_unreadable_one_gets_the_plain_message():
    b = bot(local, telegram)
    assert "I can't tell." in b.handle({"photo": [{"file_id": "a"}, {"file_id": "b"}]})
    assert b.handle({"photo": [{"file_id": "bad"}]}) == S.messages["image_failed"]          # the fake Telegram serves b"junk" for it -> 422


def test_an_unchecked_claim_gets_the_news_line_and_a_checked_one_does_not():
    unchecked = dict(CLAIM, label="UNVERIFIABLE_YET")
    def verify_unchecked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"claims": [unchecked]}) if request.url.path == "/verify" else local(request)
    out = bot(verify_unchecked, telegram).handle({"text": "x"})
    assert "latest stories from 8 major news outlets (The Hindu, NDTV)" in out and "Don't forward it" in out
    assert "headlines" not in bot(local, telegram).handle({"text": "x"})                    # the REFUTED claim is not looked up in the news
