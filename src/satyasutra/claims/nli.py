"""A local second opinion on "is this review about the same claim?", used when Gemini cannot answer (quota, outage).

A multilingual natural-language-inference model (mDeBERTa-v3, MIT, runs in the GPU service) scores each fact-check review's claim
against ours in both directions. The relation is conservative on purpose:
  same      both directions entail each other at or above the cut (a review of a stronger or weaker claim is not the same claim),
  opposite  either direction is a contradiction at or above the cut,
  otherwise different, which the rules treat as "not used to decide".
Only these three words reach the rules; the rating-to-label step stays plain code. An unreachable service leaves reviews unjudged and the
rules then use the strict word-overlap test, as before. The cut is a policy choice and is not calibrated on labelled review pairs.
"""

from __future__ import annotations

import json

import httpx

from ..config import Config
from ..schemas import Evidence
from ..schemas.core import TRUSTED_TIERS
from ..llm import GeminiClient
from .judge import Judgements, judge_reviews

ENTAIL, CONTRADICT = "entailment", "contradiction"


def relation_of(forward: list[float], backward: list[float], labels: list[str], cut: float) -> str:
    """same / opposite / different from the NLI probabilities of review -> claim (forward) and claim -> review (backward)."""
    e, c = labels.index(ENTAIL), labels.index(CONTRADICT)
    if forward[c] >= cut or backward[c] >= cut:
        return "opposite"
    if forward[e] >= cut and backward[e] >= cut:
        return "same"
    return "different"


def judge_with_nli(cfg: Config, claim_text: str, evidence: list[Evidence], http: httpx.Client | None = None) -> Judgements:
    s = cfg.runtime.nli
    out = Judgements({}, model=s.model_label, prompt_sha256="")
    candidates = [e for e in evidence if e.route == "factcheck" and e.tier in TRUSTED_TIERS and e.reviewed_claim][: s.max_reviews]
    if not candidates:
        return out
    pairs = [[e.reviewed_claim, claim_text] for e in candidates] + [[claim_text, e.reviewed_claim] for e in candidates]
    client = http or httpx.Client(timeout=s.timeout_s)
    try:
        r = client.post(f"{s.service_url}/nli", content=json.dumps({"pairs": pairs}).encode(), headers={"Content-Type": "application/json"})
        body = r.json() if r.status_code == httpx.codes.OK else None
    except (httpx.HTTPError, ValueError):
        body = None
    finally:
        if http is None:
            client.close()
    if not body or len(body.get("probs", [])) != len(pairs):
        out.error = "NliUnavailable"
        return out
    n = len(candidates)
    for i, e in enumerate(candidates):
        out.relations.setdefault(e.passage_sha256, relation_of(body["probs"][i], body["probs"][n + i], body["labels"], s.min_prob))
    return out


def judge_with_fallback(client: GeminiClient, cfg: Config, claim_text: str, evidence: list[Evidence]) -> Judgements:
    """Gemini first. When it fails (quota, outage, a bad answer) the local NLI model answers instead; when that fails too, the Gemini error stands
    and the rules fall back to the strict word-overlap test."""
    j = judge_reviews(client, cfg, claim_text, evidence)
    if not j.error:
        return j
    local = judge_with_nli(cfg, claim_text, evidence)
    if local.error:
        return j
    local.fallback = j.error
    return local
