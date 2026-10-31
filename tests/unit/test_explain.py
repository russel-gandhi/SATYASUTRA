"""SHAP-style explanations: exact Shapley values on known games, the claim attribution on the real rules, and the media detectors."""

import pytest

from satyasutra.config import load_config
from satyasutra.explain import explain_media, shapley
from satyasutra.media import label_from

from test_rules import CLAIM, NOW, cfg, claim, review, run


def test_shapley_splits_credit_exactly_in_known_games():
    both = shapley([0, 1], lambda s: int(len(s) == 2))                         # a pure AND: equal credit
    assert both == pytest.approx([0.5, 0.5])
    either = shapley([0, 1, 2], lambda s: int(0 in s))                          # only player 0 matters
    assert either == pytest.approx([1.0, 0.0, 0.0])
    values = shapley([0, 1, 2], lambda s: int(len(s) >= 2))                    # efficiency: values add up to v(all) - v(empty)
    assert sum(values) == pytest.approx(1.0)


def test_a_refuting_review_gets_the_credit_and_an_irrelevant_one_gets_none():
    deciding, other = review("False"), review("False", reviewed="A completely different statement about taxes", publisher="Pub B")
    out = run([deciding, other])
    e = {row["publisher"]: row for row in out.explanation["evidence"]}
    assert out.verdict.label == "REFUTED"
    assert e["Pub A"]["shapley"] == pytest.approx(1.0) and e["Pub A"]["decided"] and e["Pub A"]["flips_verdict_if_removed"]
    assert e["Pub B"]["shapley"] == pytest.approx(0.0) and not e["Pub B"]["decided"] and e["Pub B"]["why_not_used"]
    assert e["Pub A"]["words"]["shared"] and "taxes" in e["Pub B"]["words"]["only_in_review"]
    assert "REFUTED" in out.explanation["method"] and out.explanation["not_attributed"]


def test_no_evidence_says_what_would_change_the_label_and_the_attribution_is_empty():
    out = run([])
    assert out.explanation["evidence"] == [] and any("REFUTED" in c for c in out.explanation["counterfactuals"])


def test_two_agreeing_publishers_share_the_credit_for_high_confidence_label():
    out = run([review("False", publisher="Pub A"), review("False", publisher="Pub B")])
    values = [r["shapley"] for r in out.explanation["evidence"]]
    assert values == pytest.approx([0.5, 0.5]) and not any(r["flips_verdict_if_removed"] for r in out.explanation["evidence"])


def test_too_many_items_fall_back_to_leave_one_out_only():
    c = cfg()
    small = c.model_copy(update={"runtime": c.runtime.model_copy(update={"explain": c.runtime.explain.model_copy(update={"max_players": 1})})})
    out = run([review("False", publisher="Pub A"), review("False", publisher="Pub B")], c=small)
    assert all(r["shapley"] is None for r in out.explanation["evidence"]) and "leave-one-out" in out.explanation["method"]


def test_media_attribution_names_the_detector_that_set_the_label_and_the_margin():
    c = load_config()
    edit_cut, ai_cut = (c.thresholds.thresholds[n].value for n in (c.runtime.forensics.threshold, c.runtime.forensics.ai_threshold))
    ex = explain_media(c, {"score": edit_cut + 0.1, "ai_score": ai_cut - 0.1}, [], "EDITED", label_from)
    trufor, smaug, index = ex["signals"]
    assert trufor["over_cut"] and trufor["shapley"] == pytest.approx(1.0) and trufor["margin"] == pytest.approx(0.1)
    assert not smaug["over_cut"] and smaug["shapley"] == pytest.approx(0.0) and index["shapley"] == 0.0
    assert label_from(False, False) == "INCONCLUSIVE" and label_from(True, True) == "SYNTHETIC_SUSPECTED"
