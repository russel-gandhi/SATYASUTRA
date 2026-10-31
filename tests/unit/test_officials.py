"""The officials' view: topic by word list (English and Hindi), disaster first, state only from verdicts we already made."""

from fastapi.testclient import TestClient

from satyasutra.api.app import create_app
from satyasutra.config import load_config
from satyasutra.officials import build_view, state_of, topic_of

CFG = load_config()


def cluster(text, apm, size=5):
    return {"cluster_id": 1, "size": size, "distinct_accounts": size, "authors_per_minute": apm, "posts_per_minute": apm, "first_seen": "2026-10-31T10:00:00+00:00",
            "last_seen": "2026-10-31T10:05:00+00:00", "example_url": None, "example_text": text}


def test_topic_is_a_whole_word_match_in_english_and_hindi():
    assert topic_of("Dam burst near the city, people being evacuated", CFG) == "disaster"
    assert topic_of("बाढ़ से कई गांव डूबे", CFG) == "disaster"
    assert topic_of("Great weather today, fireworks tonight", CFG) == "general"          # 'fire' inside 'fireworks' is not a whole word
    assert topic_of("new phone launched", CFG) == "general"
    assert topic_of("watching the saffer attack vs the batting order", CFG) == "general"      # a cricket attack is not a terror attack
    assert topic_of("A terror attack was reported near the market", CFG) == "disaster"


def test_disaster_rows_come_first_even_when_a_general_one_spreads_faster():
    rows = build_view([cluster("celebrity wedding photos", 9.0), cluster("Flood warning for the river towns", 1.0), cluster("cricket final tonight", 5.0)], lambda t: None, CFG)
    assert [r["topic"] for r in rows] == ["disaster", "general", "general"] and rows[1]["authors_per_minute"] == 9.0


def test_the_state_comes_only_from_a_verdict_we_already_made():
    assert state_of(None, CFG)["checked"] is False
    body = {"claims": [{"label": "UNVERIFIABLE_YET", "confidence_band": "low", "ledger_seq": 3}, {"label": "REFUTED", "confidence_band": "medium", "ledger_seq": 4}]}
    s = state_of(body, CFG)
    assert s == {"checked": True, "label": "REFUTED", "confidence_band": "medium", "ledger_seq": 4}          # the most decisive label wins
    rows = build_view([cluster("flood claim", 2.0)], lambda t: body, CFG)
    assert rows[0]["state"]["label"] == "REFUTED"


def test_the_route_needs_the_worker_and_the_page_is_served():
    client = TestClient(create_app(load_config()))
    assert client.get("/officials/data").status_code in (200, 503)
    page = client.get("/officials")
    assert page.status_code == 200 and "What India is reading" in page.text
