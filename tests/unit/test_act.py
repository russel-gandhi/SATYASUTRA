"""Verify -> Act: the intent, the routes with their sources, the evidence pack, and the route that serves them."""

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from satyasutra import __version__
from satyasutra.act import build_pack, build_plan, load_actions
from satyasutra.api.app import create_app
from satyasutra.config import load_config
from satyasutra.ledger import SQLiteLedgerStore
from satyasutra.scam import check_links

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
SCAM = "Your Aadhaar is blocked, update KYC at https://uidai-kyc.com today"


def plan(text, labels=()):
    return build_plan(text, check_links(text), list(labels), load_actions())


def test_intent_is_chosen_by_plain_matching_and_money_lost_wins():
    assert plan("I transferred 5000 after they sent a link, I think I was scammed").intent == "money_lost"
    assert plan(SCAM).intent == "scam_forward" and "lookalike" in plan(SCAM).why
    assert plan("win a lottery prize, send your OTP").intent == "scam_forward"                       # two scam words, no link
    assert plan("send your otp").intent == "none"                                                    # one word is not enough
    assert plan("The dam has burst", labels=["REFUTED"]).intent == "false_claim"
    assert plan("The dam has burst", labels=["UNVERIFIABLE_YET"]).intent == "none"
    assert plan("मेरे पैसे कट गए, ठगी हुई").intent == "money_lost"


def test_routes_carry_their_sources_and_the_notice_says_nothing_is_filed_and_not_confirmed_on_official_sites():
    p = plan("I lost money to a scam")
    ids = [r["id"] for r in p.routes]
    assert ids[0] == "call_1930" and "cybercrime_portal" in ids and p.routes[0]["contact"] == "1930" and p.routes[0]["source_url"].startswith("https://")
    assert "do not file, send or call anything" in p.notice and "not on the official sites" in p.notice
    assert all(r["detail"] for r in p.routes)


def test_the_pack_holds_the_text_the_links_and_the_ledger_proof_and_says_we_filed_nothing(tmp_path):
    ledger = SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__)
    entry = ledger.append("verdict", {"label": "UNVERIFIABLE_YET", "confidence_band": "low", "reasons": ["no trusted review of this claim"], "evidence": []})
    a = load_actions()
    links = check_links(SCAM)
    pack = build_pack(SCAM, build_plan(SCAM, links, [], a), links, [entry.seq, 999], ledger, a, NOW)
    assert "uidai-kyc.com" in pack and "lookalike" in pack and "uidai.gov.in" in pack
    assert f"ledger entry #{entry.seq}" in pack and entry.hash in pack and "never opened" in pack
    assert "We have not filed or sent anything" in pack and "2026-10-31T12:00:00+00:00" in pack
    assert "Chakshu" in pack
    assert "No claim verdict" in build_pack(SCAM, build_plan(SCAM, links, [], a), links, [], ledger, a, NOW)


def test_the_route_returns_the_plan_and_a_pack_and_rejects_empty_text(tmp_path):
    ledger = SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__)
    seq = ledger.append("verdict", {"label": "REFUTED", "confidence_band": "medium", "reasons": ["trusted source contradicts the claim"], "evidence": []}).seq
    client = TestClient(create_app(load_config(), ledger))
    body = client.post("/act/plan", json={"text": SCAM, "ledger_seqs": [seq], "labels": ["REFUTED"]}).json()
    assert body["intent"] == "scam_forward" and body["routes"][0]["id"] == "do_not" and f"ledger entry #{seq}" in body["pack"]
    assert client.post("/act/plan", json={"text": "  "}).status_code == 422
    assert client.post("/act/plan", json={"text": "hello there", "ledger_seqs": [10 ** 6]}).json()["intent"] == "none"
