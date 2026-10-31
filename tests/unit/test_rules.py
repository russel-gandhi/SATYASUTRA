from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from satyasutra.claims.rules import classify_rating, decide, is_negated, relevance
from satyasutra.config import ConfigError, RatingMap, load_config
from satyasutra.schemas import Claim, Evidence
from satyasutra.schemas.core import sha256_text

CONFIG = Path(__file__).resolve().parents[2] / "config"
NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)
CLAIM = "The new bridge in the capital collapsed this morning killing forty people"


def cfg(supports=("true",)):
    """The real config, plus a positive rating phrase: no positive rating has been seen live yet, so the real table has none,
    but the SUPPORTED and MISSING_CONTEXT branches still need testing."""
    c = load_config(CONFIG)
    rules = c.policy.verdict_rules
    table = rules.rating_map.model_copy(update={"supports": list(supports)})
    return c.model_copy(update={"policy": c.policy.model_copy(update={"verdict_rules": rules.model_copy(update={"rating_map": table})})})


def claim(text=CLAIM, translated_by=None):
    return Claim(id="c1", post_id="p1", text_en=text, text_orig=text, span=(0, len(text)), lang="en", translated_by=translated_by)


def review(rating, reviewed=CLAIM, publisher="Pub A", tier=2, route="factcheck", reviewed_at=NOW - timedelta(days=10), url=None):
    passage = f'{publisher} rated the claim "{reviewed}" as "{rating}".'
    return Evidence(role="context", route=route, source_id="factcheck_tools", url=url or f"https://x.org/{publisher}/{rating}", fetched_at=NOW,
                    tier=tier, passage=passage, passage_sha256=sha256_text(passage), publisher=publisher, rating=rating,
                    reviewed_claim=reviewed, reviewed_at=reviewed_at)


def run(evidence, c=None, **kw):
    out = decide(claim(**kw), evidence, c or cfg(), now=lambda: NOW)
    assert "COERCED" not in out.verdict.flags          # the rules never lean on the schema's safety net
    return out


# one test per branch ------------------------------------------------------------------------------------------------

def test_no_evidence_is_unverifiable_with_low_confidence():
    out = run([])
    assert out.verdict.label == "UNVERIFIABLE_YET" and out.verdict.confidence_band == "low" and out.verdict.evidence == []
    assert "no trusted review" in out.reasons[-1]


def test_same_claim_rated_false_refutes_with_the_review_as_contradicting_evidence():
    out = run([review("False")])
    v = out.verdict
    assert v.label == "REFUTED" and v.confidence_band == "medium" and [e.role for e in v.evidence] == ["contradicts"]
    assert v.evidence[0].publisher == "Pub A" and v.axis == "claim" and v.target_id == "c1"


def test_same_claim_rated_true_supports():
    out = run([review("True")])
    assert out.verdict.label == "SUPPORTED" and [e.role for e in out.verdict.evidence] == ["supports"]


def test_conflicting_trusted_reviews_decide_nothing_and_show_both_sides():
    out = run([review("True", publisher="A"), review("False", publisher="B")])
    v = out.verdict
    assert v.label == "UNVERIFIABLE_YET" and "CONFLICT" in v.flags
    assert sorted(e.role for e in v.evidence) == ["contradicts", "supports"]


def test_support_plus_a_missing_context_review_is_missing_context():
    out = run([review("True", publisher="A"), review("Missing context", publisher="B")])
    assert out.verdict.label == "MISSING_CONTEXT" and sorted(e.role for e in out.verdict.evidence) == ["context", "supports"]


def test_a_mixed_rating_alone_cannot_decide_and_is_cited_as_context():
    out = run([review("Misleading")])
    assert out.verdict.label == "UNVERIFIABLE_YET" and [e.role for e in out.verdict.evidence] == ["context"]


def test_no_evidence_for_this_is_unproven_not_refuted():
    out = run([review("There is no evidence for this")])
    assert out.verdict.label == "UNVERIFIABLE_YET"          # absence of support never refutes (D12)


def test_an_unmapped_rating_decides_nothing_and_is_listed():
    out = run([review("Four Pinocchios")])
    assert out.verdict.label == "UNVERIFIABLE_YET" and out.unmapped_ratings == ["Four Pinocchios"]
    assert any("unmapped" in r for r in out.reasons)


def test_a_review_of_a_different_claim_is_related_context_and_never_decides():
    out = run([review("False", reviewed="Bill Gates created the new coronavirus")])
    assert out.verdict.label == "UNVERIFIABLE_YET" and out.verdict.evidence == []
    assert len(out.related) == 1 and "different claim" in out.related[0].reason


def test_a_possible_opposite_is_not_used_even_when_the_words_overlap():
    reviewed = CLAIM + " not"                               # 11 words against our 10... close enough to pass relevance
    assert relevance(CLAIM, reviewed) >= load_config(CONFIG).thresholds.thresholds["factcheck_relevance"].value
    out = run([review("False", reviewed=reviewed)])
    assert out.verdict.label == "UNVERIFIABLE_YET" and "opposite" in out.related[0].reason


def test_tier_3_and_other_routes_cannot_decide():
    out = run([review("False", tier=3), review("False", route="nli", publisher="B")])
    assert out.verdict.label == "UNVERIFIABLE_YET" and len(out.related) == 2
    assert any("tier 3" in a.reason for a in out.related) and any("not handled" in a.reason for a in out.related)


# confidence, flags, determinism -------------------------------------------------------------------------------------

def test_two_distinct_publishers_agreeing_is_high_and_one_publisher_twice_is_not():
    assert run([review("False", publisher="A"), review("Fake", publisher="B")]).verdict.confidence_band == "high"
    assert run([review("False", publisher="A", url="https://x.org/1"), review("Fake", publisher="A", url="https://x.org/2")]).verdict.confidence_band == "medium"


def test_an_old_deciding_review_is_flagged_stale():
    old = review("False", reviewed_at=NOW - timedelta(days=1000))
    assert "STALE_SOURCE" in run([old]).verdict.flags and "STALE_SOURCE" not in run([review("False")]).verdict.flags


def test_a_translated_claim_carries_the_translated_flag():
    assert "TRANSLATED" in run([review("False")], translated_by="gemini").verdict.flags


def test_the_same_inputs_give_the_same_verdict_id_and_different_evidence_gives_another():
    a, b = run([review("False")]).verdict, run([review("False")]).verdict
    assert a.id == b.id and a.id != run([review("False", publisher="Z")]).verdict.id


def test_a_related_list_is_capped_by_config_and_sorted_by_relevance():
    reviews = [review("False", reviewed=f"{CLAIM} extra{i}" + " more" * i, publisher=f"P{i}") for i in range(6)]
    out = run(reviews)
    assert len(out.related) == load_config(CONFIG).policy.verdict_rules.related_limit
    assert [a.relevance for a in out.related] == sorted((a.relevance for a in out.related), reverse=True)


# helpers ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rating,stance", [
    ("False", "refutes"), ("false and potentially dangerous", "refutes"), ("False. Decades of scientific studies", "refutes"),
    ("Mostly false", "refutes"), ("This is incorrect, the new coronavirus is a virus", "refutes"),
    ("Misleading", "mixed"), ("Half True", "mixed"), ("Missing Context", "mixed"),
    ("There is no evidence for this", "unproven"), ("We don't have good enough evidence for this yet", "unproven"), ("Unsupported", "unproven"),
    ("Four Pinocchios", "unmapped"), ("flawed paper", "unmapped"), ("", "unmapped"),
    ("True", "supports"), ("Mostly True", "supports"), ("This is correct.", "supports"),             # seen live 2026-10-31
    ("True but this job growth comes from a low base", "mixed"), ("This is not true and is a misinterpretation", "unmapped"),
])
def test_classify_rating_matches_real_ratings_by_longest_leading_phrase(rating, stance):
    assert classify_rating(rating, load_config(CONFIG)) == stance


def test_is_negated_catches_words_and_contractions_but_not_look_alikes():
    neg = load_config(CONFIG).policy.verdict_rules.negations
    assert is_negated("Smoking doesn’t cause cancer", neg) and is_negated("Smoking doesn't cause cancer", neg)
    assert is_negated("no proof US landed on moon", neg) and is_negated("works without power", neg)
    assert not is_negated("I know the answer, and nothing else", neg) and not is_negated("Smoking causes cancer", neg)


def test_relevance_is_word_overlap():
    assert relevance("The Earth is flat", "the earth is flat.") == 1.0
    assert relevance("alpha beta", "gamma delta") == 0.0 and relevance("", "") == 0.0


def test_the_rating_table_cannot_put_a_phrase_in_two_groups():
    with pytest.raises(ValueError, match="both"):
        RatingMap(refutes=["false"], mixed=["False"])


def test_the_real_config_has_the_calibrated_relevance_cut():
    entry = load_config(CONFIG).thresholds.thresholds["factcheck_relevance"]
    assert entry.value == 0.9 and "OPPOSITE" in entry.calibrated_on and entry.date
