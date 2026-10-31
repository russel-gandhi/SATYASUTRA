from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from satyasutra.schemas import (
    Claim, CorrectionCard, DetectorResult, Evidence, QualityReport, Verdict, sha256_text,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def ev(role="supports", tier=1, route="verifier", passage="The scheme pays Rs 2,000.", **kw):
    return Evidence(
        role=role, route=route, source_id="pib", url="https://pib.gov.in/x", fetched_at=NOW, tier=tier,
        passage=passage, passage_sha256=sha256_text(passage), **kw,
    )


def verdict(label, axis="claim", evidence=(), flags=()):
    return Verdict(id="v1", target_id="t1", axis=axis, label=label, confidence_band="high",
                   evidence=list(evidence), flags=list(flags))


# ---- claim axis -------------------------------------------------------------------------

def test_empty_evidence_forces_unverifiable_yet():
    v = verdict("SUPPORTED")
    assert v.label == "UNVERIFIABLE_YET" and "COERCED" in v.flags and v.confidence_band == "low"


def test_supported_with_trusted_support_stands():
    assert verdict("SUPPORTED", evidence=[ev("supports", tier=1)]).label == "SUPPORTED"
    assert verdict("SUPPORTED", evidence=[ev("supports", tier=2)]).label == "SUPPORTED"


def test_low_tier_evidence_cannot_decide():
    assert verdict("SUPPORTED", evidence=[ev("supports", tier=3)]).label == "UNVERIFIABLE_YET"
    assert verdict("REFUTED", evidence=[ev("contradicts", tier=4)]).label == "UNVERIFIABLE_YET"


def test_refuted_needs_a_contradiction_not_silence():
    assert verdict("REFUTED", evidence=[ev("context", tier=1)]).label == "UNVERIFIABLE_YET"
    assert verdict("REFUTED", evidence=[ev("supports", tier=1)]).label == "UNVERIFIABLE_YET"
    assert verdict("REFUTED", evidence=[ev("contradicts", tier=1, route="contrast",
                                           contrast_claim="The scheme pays Rs 2,000.")]).label == "REFUTED"


def test_missing_context_needs_both_sides():
    assert verdict("MISSING_CONTEXT", evidence=[ev("supports")]).label == "UNVERIFIABLE_YET"
    assert verdict("MISSING_CONTEXT", evidence=[ev("supports"), ev("context", passage="Only for farmers.")]).label == "MISSING_CONTEXT"


def test_not_a_claim_and_unverifiable_need_no_evidence():
    assert verdict("NOT_A_CLAIM").label == "NOT_A_CLAIM"
    v = verdict("UNVERIFIABLE_YET")
    assert v.label == "UNVERIFIABLE_YET" and "COERCED" not in v.flags


# ---- media axis -------------------------------------------------------------------------

def test_media_label_on_claim_axis_is_rejected():
    with pytest.raises(ValidationError):
        verdict("SYNTHETIC", axis="claim")
    with pytest.raises(ValidationError):
        verdict("REFUTED", axis="media")


def test_media_without_evidence_is_inconclusive():
    for label in ("SYNTHETIC", "EDITED", "OUT_OF_CONTEXT", "AUTHENTIC_IN_CONTEXT"):
        assert verdict(label, axis="media").label == "INCONCLUSIVE"


def test_out_of_context_needs_an_earlier_appearance():
    assert verdict("OUT_OF_CONTEXT", axis="media", evidence=[ev("context", tier=3, route="provenance")]).label == "INCONCLUSIVE"
    good = verdict("OUT_OF_CONTEXT", axis="media", evidence=[ev("earlier_appearance", tier=3, route="provenance")])
    assert good.label == "OUT_OF_CONTEXT"


def test_no_hit_is_never_authentic():
    # I11: a detector finding nothing is not provenance
    d = ev("supports", tier=3, route="detector")
    assert verdict("AUTHENTIC_IN_CONTEXT", axis="media", evidence=[d]).label == "INCONCLUSIVE"
    p = ev("supports", tier=1, route="provenance")
    assert verdict("AUTHENTIC_IN_CONTEXT", axis="media", evidence=[p]).label == "AUTHENTIC_IN_CONTEXT"


def test_unknown_flag_rejected():
    with pytest.raises(ValidationError):
        verdict("UNVERIFIABLE_YET", flags=["MADE_UP"])


# ---- evidence ---------------------------------------------------------------------------

def test_evidence_hash_must_match_passage():
    with pytest.raises(ValidationError, match="passage_sha256"):
        Evidence(role="supports", route="verifier", source_id="s", url="https://x", fetched_at=NOW, tier=1,
                 passage="real text", passage_sha256=sha256_text("other text"))


def test_contrast_route_requires_contrast_claim():
    with pytest.raises(ValidationError, match="contrast_claim"):
        ev("contradicts", route="contrast")


def test_support_prob_bounds():
    with pytest.raises(ValidationError):
        ev(support_prob=1.5)


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        Verdict(id="v", target_id="t", axis="claim", label="NOT_A_CLAIM", surprise=1)


# ---- detector result --------------------------------------------------------------------

def test_failed_gate_cannot_carry_a_score():
    bad = QualityReport(passed=False, reasons=["too_short"])
    with pytest.raises(ValidationError, match="abstain"):
        DetectorResult(detector="audio.x", modality="audio", version="1", abstained=False, score=0.9, quality=bad)
    with pytest.raises(ValidationError, match="score"):
        DetectorResult(detector="audio.x", modality="audio", version="1", abstained=True, score=0.9, quality=bad)
    ok = DetectorResult(detector="audio.x", modality="audio", version="1", abstained=True, quality=bad)
    assert ok.score is None


def test_answering_detector_needs_score_and_passed_gate():
    good = QualityReport(passed=True)
    with pytest.raises(ValidationError, match="score"):
        DetectorResult(detector="d", modality="image", version="1", abstained=False, quality=good)
    assert DetectorResult(detector="d", modality="image", version="1", abstained=False, score=0.2, quality=good).score == 0.2


def test_failed_quality_report_must_explain():
    with pytest.raises(ValidationError, match="say why"):
        QualityReport(passed=False)


# ---- other ------------------------------------------------------------------------------

def test_claim_span_validated():
    with pytest.raises(ValidationError):
        Claim(id="c", post_id="p", text_en="a", text_orig="a", span=(5, 2), lang="en")


def test_correction_card_links_must_be_https():
    with pytest.raises(ValidationError):
        CorrectionCard(verdict_id="v", lang="hi", headline="h", sentence="s", links=["http://x"], checked_at=NOW)
    with pytest.raises(ValidationError):
        CorrectionCard(verdict_id="v", lang="hi", headline="h", sentence="s", links=[], checked_at=NOW)
    assert CorrectionCard(verdict_id="v", lang="hi", headline="h", sentence="s", links=["https://pib.gov.in"], checked_at=NOW)


def test_deciding_record_itself_must_be_trusted():
    # D35: a Tier 4 "supports" cannot borrow trust from an unrelated Tier 1 record
    v = verdict("SUPPORTED", evidence=[ev("supports", tier=4), ev("context", tier=1, passage="Only for farmers.")])
    assert v.label == "UNVERIFIABLE_YET" and "COERCED" in v.flags
    assert verdict("MISSING_CONTEXT", evidence=[ev("supports", tier=1), ev("context", tier=4)]).label == "UNVERIFIABLE_YET"


def test_trusted_support_and_contradiction_is_a_conflict():
    # Bible 8.4: both sides -> UNVERIFIABLE_YET with CONFLICT
    for label in ("SUPPORTED", "REFUTED"):
        v = verdict(label, evidence=[ev("supports"), ev("contradicts", passage="It pays Rs 1,000.")])
        assert v.label == "UNVERIFIABLE_YET" and {"CONFLICT", "COERCED"} <= set(v.flags) and v.confidence_band == "low"
    # a low-tier contradiction is context only and does not cause a conflict
    assert verdict("SUPPORTED", evidence=[ev("supports"), ev("contradicts", tier=4)]).label == "SUPPORTED"
