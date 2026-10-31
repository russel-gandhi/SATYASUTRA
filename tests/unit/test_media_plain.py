"""The image check in plain words: the right outcome for each real result, honest wording, and the numbers shown are the real ones."""

from pathlib import Path

import pytest

from satyasutra.config import load_config
from satyasutra.media_plain import KINDS, build_summary, load_plain, outcome_kind

CONFIG = Path(__file__).resolve().parents[2] / "config"
EDIT_CUT, AI_CUT = 0.7, 0.5
MATCH = [{"publisher": "Alt News", "title": "t", "article_url": "https://x.org/a", "distance": 3}]


def summary(score=0.09, ai=0.0, matches=(), available=True, ai_error=""):
    forensics = {"available": available, "score": score, "ai_score": ai, "ai_error": ai_error} if available else {"available": False, "reason": "forensics service unreachable"}
    return build_summary(width=640, height=480, forensics=forensics, matches=list(matches), edit_cut=EDIT_CUT, ai_cut=AI_CUT)


def test_clean_result_is_clear_and_never_says_genuine_or_authentic():
    s = summary()
    assert s["kind"] == "clean" and s["badge"] == "NOTHING FOUND" and s["headline"] == "No sign of editing or AI generation"
    assert "not proof" in s["meaning"]
    text = " ".join([s["headline"], s["meaning"], *s["cannot_say"], *(st["result"] for st in s["steps"])]).lower()
    assert "authentic" not in text.replace("never report 'genuine'", "")             # the rule line may name the word it refuses to use
    assert all(st["status"] == "done" for st in s["steps"])


def test_the_steps_are_the_real_stages_in_order_with_the_real_numbers():
    s = summary(score=0.09, ai=0.0)
    assert [st["id"] for st in s["steps"]] == ["read", "history", "edit", "ai", "rule"]
    by = {st["id"]: st for st in s["steps"]}
    assert "640 by 480" in by["read"]["result"]
    assert "0.09" in by["edit"]["result"] and "0.7" in by["edit"]["result"]
    assert "0.00" in by["ai"]["result"] and "0.5" in by["ai"]["result"]
    assert len(s["found"]) == 3


@pytest.mark.parametrize("kwargs,kind,status_edit,status_ai", [
    ({"score": 0.95}, "edited", "flag", "done"),
    ({"ai": 0.8}, "ai_suspected", "done", "flag"),
    ({"score": 0.95, "ai": 0.8}, "both", "flag", "flag"),
    ({"matches": MATCH}, "known_image", "done", "done"),
])
def test_each_flag_gives_its_own_outcome(kwargs, kind, status_edit, status_ai):
    s = summary(**kwargs)
    by = {st["id"]: st for st in s["steps"]}
    assert s["kind"] == kind and by["edit"]["status"] == status_edit and by["ai"]["status"] == status_ai


def test_edit_flag_beats_a_fact_check_match_and_both_are_reported():
    s = summary(score=0.95, matches=MATCH)
    by = {st["id"]: st for st in s["steps"]}
    assert s["kind"] == "edited" and by["history"]["status"] == "flag" and "1 fact-check article" in by["history"]["result"]


def test_a_lone_ai_hit_stays_a_suspicion():
    s = summary(ai=0.9)
    assert "suspicion" in " ".join(st["result"] for st in s["steps"] if st["id"] == "rule") and "not proof" in s["meaning"]


def test_service_down_says_nothing_was_checked_and_nothing_guessed():
    s = summary(available=False)
    by = {st["id"]: st for st in s["steps"]}
    assert s["kind"] == "unavailable" and by["edit"]["status"] == "skip" and by["ai"]["status"] == "skip"
    assert "nothing was guessed" in s["meaning"]


def test_service_down_but_a_fact_check_match_still_reports_the_match():
    assert summary(available=False, matches=MATCH)["kind"] == "known_image"


def test_missing_detector_is_skipped_with_its_reason_not_reported_as_clean():
    s = summary(ai=None, ai_error="Smaug weights are not installed")
    ai = {st["id"]: st for st in s["steps"]}["ai"]
    assert ai["status"] == "skip" and "Smaug weights are not installed" in ai["result"]


def test_no_calibrated_cut_means_no_flag_and_a_skipped_step():
    s = build_summary(width=10, height=10, forensics={"available": True, "score": 0.99, "ai_score": 0.99}, matches=[], edit_cut=None, ai_cut=None)
    by = {st["id"]: st for st in s["steps"]}
    assert s["kind"] == "clean" and by["edit"]["status"] == "skip" and by["ai"]["status"] == "skip"


def test_every_outcome_has_words_and_the_do_next_help_is_there():
    plain = load_plain()
    for kind in KINDS:
        assert plain.badges[kind] and plain.headlines[kind] and plain.meanings[kind] and plain.cannot_say[kind] and plain.steps["rule"]["result"][kind]
    s = summary()
    assert s["do_next"]["tools"] and all(t["url"].startswith("https://") for t in s["do_next"]["tools"]) and s["do_next"]["tips"]


def test_outcome_rule_priority():
    assert outcome_kind(available=True, edited=True, ai_flagged=True, matched=True) == "both"
    assert outcome_kind(available=False, edited=False, ai_flagged=False, matched=False) == "unavailable"
    assert outcome_kind(available=True, edited=False, ai_flagged=False, matched=False) == "clean"


def test_the_api_returns_the_summary_for_a_real_image(tmp_path):
    import io
    import numpy as np
    from fastapi.testclient import TestClient
    from PIL import Image

    from satyasutra import __version__
    from satyasutra.api.app import create_app
    from satyasutra.ledger import SQLiteLedgerStore
    from satyasutra.provenance import ImageIndex

    out = io.BytesIO()
    Image.fromarray(np.random.default_rng(3).integers(0, 255, (96, 96, 3), dtype=np.uint8)).save(out, format="PNG")
    analyse = lambda c, d: {"available": True, "score": 0.09, "ai_score": 0.0, "device": "cpu", "seconds": 0.1, "width": 96, "height": 96, "heatmap_png_b64": "AAAA"}
    app = create_app(load_config(CONFIG), SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__), analyse_image=analyse, image_index=ImageIndex(tmp_path / "i.sqlite3"))
    body = TestClient(app).post("/media/check", content=out.getvalue()).json()
    assert body["label"] == "INCONCLUSIVE"                     # the record keeps its careful label
    assert body["summary"]["kind"] == "clean" and body["summary"]["headline"] == "No sign of editing or AI generation"
    assert [s["id"] for s in body["summary"]["steps"]][-1] == "rule"
