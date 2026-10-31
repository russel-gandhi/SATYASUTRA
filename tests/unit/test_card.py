"""Correction cards: who gets one, which language, what it carries, and the route."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from satyasutra.api.app import create_app
from satyasutra.card import build_card, card_language, load_cards
from satyasutra.config import load_config

CARDS = load_cards()
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
CLAIM = {"claim_id": "c1", "text": "Vaccines contain microchips.", "quote": "वैक्सीन में माइक्रोचिप है", "lang": "en", "label": "REFUTED", "confidence_band": "medium",
         "ledger_seq": 7, "citations": [{"publisher": "FactCheck.org", "rating": "False", "url": "https://example.org/a"}, {"url": "http://insecure.example/x"}]}


def test_a_refuted_claim_gets_an_english_card_with_the_source_and_the_record_number():
    card = build_card(CLAIM, CARDS, NOW)
    assert card["headline"] == "FALSE" and card["lang"] == "en" and card["claim"] == CLAIM["text"]
    assert card["sentence"] == 'FactCheck.org checked this claim and rated it "False".' and card["links"] == ["https://example.org/a"] and "Record #7" in card["ledger"]
    assert "image" not in card and "media" not in card                         # never the original media


def test_a_hindi_claim_gets_a_hindi_card_with_the_original_words():
    card = build_card(dict(CLAIM, lang="hi"), CARDS, NOW)
    assert card["lang"] == "hi" and card["headline"] == "झूठ" and card["claim"] == CLAIM["quote"] and "जाँच" in card["sentence"] and card_language("hi-IN") == "hi"


def test_undecided_low_confidence_or_sourceless_claims_get_no_card():
    assert build_card(dict(CLAIM, label="UNVERIFIABLE_YET"), CARDS, NOW) is None
    assert build_card(dict(CLAIM, confidence_band="low"), CARDS, NOW) is None
    assert build_card(dict(CLAIM, citations=[{"url": "http://insecure.example/x"}]), CARDS, NOW) is None


def test_the_route_returns_the_card_or_409_and_the_font_is_served():
    client = TestClient(create_app(load_config()))
    assert client.post("/card/spec", json={"claim": CLAIM}).json()["headline"] == "FALSE"
    assert client.post("/card/spec", json={"claim": dict(CLAIM, label="UNVERIFIABLE_YET")}).status_code == 409
    font = client.get("/assets/fonts/NotoSansDevanagari.ttf")
    assert font.status_code == 200 and len(font.content) > 10_000
