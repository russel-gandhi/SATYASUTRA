"""When the language model cannot answer, people are told the truth: a daily limit is not a one-minute wait, and an error is never reported as 'no claim found'."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from satyasutra import __version__
from satyasutra.api.app import create_app
from satyasutra.bot import TelegramBot
from satyasutra.claims import ExtractionResult, Judgements
from satyasutra.config import load_config
from satyasutra.ledger import SQLiteLedgerStore
from satyasutra.sources import FactCheckResult

ROOT = Path(__file__).resolve().parents[2]
CFG = load_config()
V = CFG.runtime.verify
S = CFG.runtime.telegram


def api(tmp_path, reason):
    def extract(post):
        return ExtractionResult("failed", [], reason=reason, model="m", prompt_sha256="p")

    return TestClient(create_app(CFG, SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__), extract=extract,
                                 lookup=lambda t, l: FactCheckResult([], 0, False), judge=lambda t, e: Judgements({}, "m", "p")))


# ---- the words ------------------------------------------------------------------------------------------------------------------

def test_the_two_messages_are_different_and_the_daily_one_does_not_promise_a_minute():
    assert V.busy_message != V.daily_limit_message
    assert "about a minute" in V.busy_message
    assert "daily" in V.daily_limit_message and "will not help" in V.daily_limit_message and "about a minute" not in V.daily_limit_message
    for text in (V.busy_message, V.daily_limit_message):
        assert "nothing was checked and nothing was guessed" in text


# ---- the API --------------------------------------------------------------------------------------------------------------------

def test_a_used_up_daily_allowance_is_a_503_that_says_so(tmp_path):
    r = api(tmp_path, "QuotaExhausted: daily quota used up").post("/verify", json={"text": "Some claim about a bridge"})
    assert r.status_code == 503 and r.json()["detail"] == V.daily_limit_message


@pytest.mark.parametrize("reason", ["RateLimited: HTTP 429, retry delay 30", "Unreachable: timed out"])
def test_a_request_limit_or_outage_is_a_503_that_says_try_again_shortly(tmp_path, reason):
    r = api(tmp_path, reason).post("/verify", json={"text": "Some claim about a bridge"})
    assert r.status_code == 503 and r.json()["detail"] == V.busy_message


def test_a_different_failure_is_still_a_502_with_its_reason(tmp_path):
    r = api(tmp_path, "BadOutput: model output is not valid JSON").post("/verify", json={"text": "Some claim about a bridge"})
    assert r.status_code == 502 and "claim extraction failed" in r.json()["detail"]


# ---- the web page -----------------------------------------------------------------------------------------------------------------

def test_the_page_has_its_own_sentence_for_a_daily_limit():
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'e.data.type === "ModelDailyLimit"' in html and "Waiting a minute will not help" in html
    assert html.index("ModelDailyLimit") < html.index('e.data.type === "ModelBusy"')       # the more specific class is tested first


# ---- the Telegram bot ----------------------------------------------------------------------------------------------------------

def bot_with(status, body):
    seen: list[str] = []

    def local(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/act/plan":                       # asked first, to see whether the message is about private images
            return httpx.Response(200, json={"intent": "none", "why": "", "notice": "", "routes": []})
        return httpx.Response(status, json=body)

    b = TelegramBot(CFG, token="t0k")
    b.local = httpx.Client(base_url="http://local", transport=httpx.MockTransport(local))
    return b, seen


def test_the_bot_repeats_the_daily_limit_message_and_does_nothing_else():
    b, seen = bot_with(503, {"detail": V.daily_limit_message})
    assert b.check_text("Some claim about a bridge") == V.daily_limit_message
    assert seen == ["/act/plan", "/verify"]                       # the topic check first, then the failed check, and nothing after it (no news lookup, no next steps)


def test_the_bot_never_says_no_claim_was_found_when_the_check_could_not_run():
    for status, body in ((503, {"detail": V.busy_message}), (502, {"detail": "claim extraction failed: x"}), (500, {"detail": "oops"}), (503, {})):
        b, _ = bot_with(status, body)
        out = b.check_text("Some claim about a bridge")
        assert out in (V.busy_message, S.messages["error"])
        assert S.messages["none_found"] not in out and "Opinions and chatter" not in out


def test_an_error_page_that_is_not_json_is_still_an_error_reply():
    seen = []

    def local(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/act/plan":
            return httpx.Response(200, json={"intent": "none", "why": "", "notice": "", "routes": []})
        return httpx.Response(503, content=b"<html>gateway</html>")

    b = TelegramBot(CFG, token="t0k")
    b.local = httpx.Client(base_url="http://local", transport=httpx.MockTransport(local))
    assert b.check_text("Some claim about a bridge") == S.messages["error"] and seen == ["/act/plan", "/verify"]
