"""NLI as the local fallback judge: the relation rule, the service call, and the order Gemini first, NLI second, word overlap last."""

import json
from datetime import datetime, timezone

import httpx

from satyasutra.claims import Judgements, judge_with_fallback, judge_with_nli
from satyasutra.claims.nli import relation_of
from satyasutra.config import load_config
from satyasutra.errors import QuotaExhausted
from satyasutra.schemas import Evidence
from satyasutra.schemas.core import sha256_text

CFG = load_config()
LABELS = ["entailment", "neutral", "contradiction"]
NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)
CUT = CFG.runtime.nli.min_prob


def review(text: str) -> Evidence:
    passage = f'Pub rated the claim "{text}" as "False".'
    return Evidence(role="context", route="factcheck", source_id="factcheck_tools", url="https://example.org/a", fetched_at=NOW, tier=2, passage=passage,
                    passage_sha256=sha256_text(passage), publisher="Pub", rating="False", reviewed_claim=text)


def test_same_needs_both_directions_to_entail_and_any_contradiction_is_opposite():
    hi, lo = [0.99, 0.01, 0.0], [0.0, 1.0, 0.0]
    assert relation_of(hi, hi, LABELS, CUT) == "same"
    assert relation_of(hi, lo, LABELS, CUT) == "different"                 # a stronger or weaker claim is not the same claim
    assert relation_of(lo, lo, LABELS, CUT) == "different"
    assert relation_of(lo, [0.0, 0.02, 0.98], LABELS, CUT) == "opposite"
    assert relation_of([0.95, 0.04, 0.01], [0.85, 0.1, 0.05], LABELS, CUT) == "different"       # below the cut in one direction


def service(rows):
    def handler(request: httpx.Request) -> httpx.Response:
        pairs = json.loads(request.content)["pairs"]
        return httpx.Response(200, json={"labels": LABELS, "probs": rows(pairs)})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_each_review_is_scored_in_both_directions_and_mapped_to_a_relation():
    evidence = [review("Vaccines contain microchips"), review("Vaccines do not contain microchips")]
    def rows(pairs):                                   # pairs: reviews -> claim (2), then claim -> reviews (2)
        return [[0.99, 0.01, 0.0], [0.0, 0.01, 0.99], [0.99, 0.01, 0.0], [0.0, 0.01, 0.99]]
    j = judge_with_nli(CFG, "Vaccines contain microchips", evidence, service(rows))
    assert j.relations == {evidence[0].passage_sha256: "same", evidence[1].passage_sha256: "opposite"} and not j.error and j.model == CFG.runtime.nli.model_label


def test_an_unreachable_service_leaves_reviews_unjudged_with_an_error_class():
    def down(request):
        raise httpx.ConnectError("refused")
    j = judge_with_nli(CFG, "x claim here", [review("x claim here")], httpx.Client(transport=httpx.MockTransport(down)))
    assert j.relations == {} and j.error == "NliUnavailable"


class DownGemini:
    model = "g"
    def generate_json(self, *a, **k):
        raise QuotaExhausted("gemini_llm", "daily quota used up")


def test_when_gemini_fails_the_local_model_judges_and_says_why(monkeypatch):
    ev = [review("Vaccines contain microchips")]
    monkeypatch.setattr("satyasutra.claims.nli.judge_with_nli", lambda cfg, text, evidence: Judgements({ev[0].passage_sha256: "same"}, model="nli"))
    j = judge_with_fallback(DownGemini(), CFG, "Vaccines contain microchips", ev)
    assert j.relations and j.fallback == "QuotaExhausted" and not j.error


def test_when_both_fail_the_gemini_error_stands_so_the_word_overlap_rule_applies(monkeypatch):
    ev = [review("Vaccines contain microchips")]
    monkeypatch.setattr("satyasutra.claims.nli.judge_with_nli", lambda cfg, text, evidence: Judgements({}, error="NliUnavailable"))
    j = judge_with_fallback(DownGemini(), CFG, "Vaccines contain microchips", ev)
    assert j.error == "QuotaExhausted" and j.relations == {} and not j.fallback
