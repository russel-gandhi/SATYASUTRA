"""Scam Guard (SG1, SG2): signals, the negation guard, states, and that nothing is stored."""

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from satyasutra.api.app import create_app
from satyasutra.config import load_config
from satyasutra.scam.script import Call, judge, load_script, signals_in
from satyasutra.scam.session import SessionFull, Sessions
from satyasutra.voice import Transcript

RULES = load_script()
SID = "abcd1234efgh"


def run(*chunks):
    call = Call(RULES)
    return [call.add(c) for c in chunks]


def test_a_digital_arrest_script_turns_red_with_its_type():
    v = run("This is the cyber cell. A case has been registered and there is a warrant, you will be arrested.")[-1]
    assert v.state == "scam" or v.state == "careful"
    v = run("This is the police.", "There is a case registered against your Aadhaar, we will arrest you.", "Transfer the money to the safe account now.")[-1]
    assert v.state == "scam" and v.scam_type in ("digital_arrest", "payment_demand")


@pytest.mark.parametrize("text", ["Please tell me the OTP you just received.", "install AnyDesk and share your screen", "otp batao abhi"])
def test_a_hard_signal_alone_is_red(text):
    assert run(text)[-1].state == "scam"


@pytest.mark.parametrize("text", [
    "We will never ask you to share your OTP or PIN with anyone.",
    "Please do not share your OTP with anyone, even bank staff.",
    "बैंक आपसे ओटीपी कभी नहीं मांगता, इसे किसी से साझा न करें।",
])
def test_a_real_banks_warning_does_not_alert(text):
    assert run(text)[-1].state == "listening" and signals_in(text, RULES) == set()


def test_the_negation_only_covers_its_own_sentence():
    assert "credential_request" in signals_in("We never ask for card details. But now tell me your OTP.", RULES)


def test_two_different_soft_signals_make_amber_and_ordinary_talk_stays_listening():
    assert run("Do not tell anyone about this, it is urgent, do it right now.")[-1].state == "careful"
    assert run("Hello, your parcel is at the gate, please come down to collect it.")[-1].state == "listening"


def test_signals_fade_after_the_window():
    call = Call(RULES)
    call.add("There is a warrant and we will arrest you")
    for _ in range(RULES.settings.window_chunks):
        v = call.add("okay thank you, have a good day")
    assert v.state == "listening"


def test_the_page_state_never_says_safe():
    for s in RULES.states.values():
        assert "safe" not in s.en.lower().replace("safe account", "")


def test_sessions_expire_and_the_count_is_capped():
    s = Sessions(RULES)
    s.get("aaaaaaaa", now=0.0)
    s.get("bbbbbbbb", now=RULES.settings.session_s + 5)
    assert len(s) == 1
    full = Sessions(RULES)
    for i in range(RULES.settings.max_sessions):
        full.get(f"s{i:07d}", now=1.0)
    with pytest.raises(SessionFull):
        full.get("onetoomany", now=1.0)


def test_the_endpoint_writes_nothing_to_disk_or_any_record(tmp_path, monkeypatch):
    cfg = load_config().model_copy(deep=True)
    cfg.runtime.report.db_path = str(tmp_path / "records.sqlite3")
    app = create_app(cfg, transcribe=lambda d: Transcript("tell me your OTP right now", "en", 0.99, False, 3.0, 0.2, "fake"))
    c = TestClient(app)
    before = sorted(p.name for p in tmp_path.iterdir())
    out = c.post("/guard/chunk", content=b"audio-bytes", headers={"x-session": SID}).json()
    assert out["state"] == "scam" and out["message"]["en"]
    assert c.post("/guard/text", json={"session": SID, "text": "hello there"}).json()["state"] == "scam"       # same call: the window still holds the alert
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert c.delete(f"/guard/{SID}").json() == {"ended": True}
    assert c.post("/guard/text", json={"session": SID, "text": "hello there"}).json()["state"] == "listening"
    assert c.post("/guard/chunk", content=b"", headers={"x-session": SID}).status_code == 422


def test_every_scam_type_has_advice_in_both_languages():
    rules = load_script()
    assert set(rules.types) <= set(rules.advice)
    assert all(a.en.strip() and a.hi.strip() for a in rules.advice.values())
