"""A short death or arrest rumour must not be dropped as 'chatter', and a message with no claim must say how it was read."""

from datetime import datetime, timezone

import pytest

from satyasutra.bot import format_claims
from satyasutra.claims.extract import build_prompt, load_prompt, validate_items
from satyasutra.config import load_config
from satyasutra.schemas import Post

CFG = load_config()
EX = CFG.runtime.extract
M = CFG.runtime.telegram.messages


def post(text):
    now = datetime.now(timezone.utc)
    return Post(id="p1", source_id="s", url="", author_hash="h" * 64, text=text, posted_at=now, fetched_at=now)


def item(quote, claim_en=None, kind="other", worth=False, lang="en"):
    return {"quote": quote, "claim_en": claim_en or quote, "lang": lang, "kind": kind, "checkworthy": worth, "time_ref": "", "place_ref": ""}


def run(text, items, **kw):
    promoted: list[str] = []
    claims, others = validate_items(post(text), {"items": items}, 5, "m", EX.promote_terms, EX.promote_kinds, promoted, **kw)
    return claims, others, promoted


# ---- the failure from the screenshot --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind,worth", [("other", False), ("question", False), ("factual_claim", False)])
def test_a_death_rumour_filed_as_chatter_is_kept_as_a_claim(kind, worth):
    claims, others, promoted = run("Nana Patekar is dead", [item("Nana Patekar is dead", "Nana Patekar is dead.", kind, worth)])
    assert [c.text_en for c in claims] == ["Nana Patekar is dead."] and others == [] and promoted == ["Nana Patekar is dead"]


def test_the_question_form_is_kept_too():
    claims, _, promoted = run("Nana Patekar is dead?", [item("Nana Patekar is dead?", "Is Nana Patekar dead?", "question")])
    assert len(claims) == 1 and promoted == ["Nana Patekar is dead?"]


def test_other_high_stakes_rumours_and_hindi_are_kept():
    for text in ("Rahul Dravid arrested in Mumbai", "The minister has resigned", "Famous singer killed in accident", "अभिनेता का निधन हो गया"):
        claims, _, _ = run(text, [item(text, "A claim.", "other")])
        assert len(claims) == 1, text


# ---- what must not change -------------------------------------------------------------------------------------------------------

def test_opinions_and_personal_experience_are_never_promoted():
    text = "This film is dead boring. I died laughing"
    claims, others, promoted = run(text, [item("This film is dead boring", "The author finds the film boring.", "opinion"), item("I died laughing", "The author laughed.", "personal_experience")])
    assert claims == [] and promoted == [] and [o.kind for o in others] == ["opinion", "personal_experience"]


def test_ordinary_chatter_without_a_high_stakes_word_stays_chatter():
    claims, others, promoted = run("Good morning everyone", [item("Good morning everyone", "A greeting.", "other")])
    assert claims == [] and promoted == [] and others[0].kind == "other"


def test_a_word_inside_another_word_does_not_trigger_it():
    claims, _, _ = run("The deadline is tomorrow", [item("The deadline is tomorrow", "A deadline.", "other")])
    assert claims == []


def test_without_the_settings_nothing_is_promoted():
    claims, others = validate_items(post("X is dead"), {"items": [item("X is dead", "X is dead.", "other")]}, 5, "m")
    assert claims == [] and others[0].kind == "other"


def test_a_decided_claim_is_unchanged_and_not_counted_as_promoted():
    claims, _, promoted = run("The RBI rate is 6.5%", [item("The RBI rate is 6.5%", "The RBI rate is 6.5%.", "factual_claim", True)])
    assert len(claims) == 1 and promoted == []


# ---- the prompt asks for it too -----------------------------------------------------------------------------------------------

def test_prompt_tells_the_model_questions_and_death_rumours_are_claims():
    prompt = build_prompt(load_prompt(), "Nana Patekar is dead?")
    assert "Is X dead?" in prompt and "never call them opinion or other" in prompt and "Nana Patekar is dead?" in prompt


# ---- the reply says how the message was read ----------------------------------------------------------------------------------------

def test_reply_for_a_message_the_model_read_as_not_a_fact_says_so_and_how_to_rephrase():
    body = {"claims": [], "extraction": {"status": "ok", "non_claim_items": [{"quote": "lol he is dead", "kind": "opinion", "paraphrase": "x"}]}}
    out = format_claims(body, CFG.runtime.telegram.labels, M)
    assert 'an opinion: "lol he is dead"' in out and "send it as a statement" in out and "Opinions and chatter" not in out


def test_reply_when_the_model_output_was_unreadable_says_nothing_was_guessed():
    out = format_claims({"claims": [], "extraction": {"status": "invalid_output", "non_claim_items": []}}, CFG.runtime.telegram.labels, M)
    assert out == M["could_not_read"] and "guessed nothing" in out


def test_reply_with_no_information_is_the_old_message_and_long_quotes_are_cut():
    assert format_claims({"claims": []}, CFG.runtime.telegram.labels, M) == M["none_found"]
    long = {"claims": [], "extraction": {"status": "ok", "non_claim_items": [{"quote": "word " * 200, "kind": "weird_kind", "paraphrase": ""}]}}
    out = format_claims(long, CFG.runtime.telegram.labels, M)
    assert "a remark" in out and len(out) < 500


# ---- through the real API with a stand-in model -------------------------------------------------------------------------------------

def test_the_verify_response_carries_what_the_model_made_of_the_message(tmp_path):
    from fastapi.testclient import TestClient

    from satyasutra import __version__
    from satyasutra.api.app import create_app
    from satyasutra.claims import ExtractionResult, Judgements
    from satyasutra.ledger import SQLiteLedgerStore
    from satyasutra.claims.extract import NonClaim
    from satyasutra.sources import FactCheckResult

    def extract(p):
        return ExtractionResult("ok", [], [NonClaim("hmm", "A remark.", "other", "en")], model="m", prompt_sha256="p")

    app = create_app(CFG, SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__), extract=extract, lookup=lambda t, l: FactCheckResult([], 0, False),
                     judge=lambda t, e: Judgements({}, "m", "p"))
    body = TestClient(app).post("/verify", json={"text": "hmm"}).json()
    assert body["claims"] == [] and body["extraction"]["non_claim_items"] == [{"quote": "hmm", "kind": "other", "paraphrase": "A remark."}]
    assert "I read your message as a remark" in format_claims(body, CFG.runtime.telegram.labels, M)
