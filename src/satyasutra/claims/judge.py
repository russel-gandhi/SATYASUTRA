"""Does a fact-check review cover the same claim as ours? Gemini answers that one question, and nothing else.

Word overlap cannot tell a claim from its opposite or from a paraphrase (config/thresholds.yaml, factcheck_relevance), so the
rules ask this judge. It only picks one of three relations per review; the rating-to-label step stays plain code. Any problem
(a failed call, a bad answer, a missing review) leaves that review unjudged, and the rules then use the strict word-overlap
test for it, so this module can only add decisions, never silently change an existing one.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from ..errors import SourceError
from ..llm import GeminiClient
from ..schemas import Evidence
from ..schemas.core import TRUSTED_TIERS, sha256_text
from .extract import load_prompt

RELATIONS = ("same", "opposite", "different")
REVIEWS_OPEN, REVIEWS_CLOSE = "<<<REVIEWS", "REVIEWS>>>"
CLAIM_OPEN, CLAIM_CLOSE = "<<<CLAIM", "CLAIM>>>"
PROMPT = "judge_reviews.txt"

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"review": {"type": "INTEGER"}, "relation": {"type": "STRING", "enum": list(RELATIONS)}},
                "required": ["review", "relation"],
            },
        }
    },
    "required": ["items"],
}


@dataclass
class Judgements:
    relations: dict[str, str]            # passage_sha256 -> same, opposite or different; absent means unjudged
    model: str = ""
    prompt_sha256: str = ""
    error: str = ""                      # exception class when the call failed; the rules then fall back


def _clean(text: str) -> str:
    for marker in (REVIEWS_OPEN, REVIEWS_CLOSE, CLAIM_OPEN, CLAIM_CLOSE):
        text = text.replace(marker, "")
    return " ".join(text.split())


def judge_reviews(client: GeminiClient, config: Config, claim_text: str, evidence: list[Evidence]) -> Judgements:
    template = load_prompt(PROMPT)
    out = Judgements({}, client.model, sha256_text(template))
    candidates = [e for e in evidence if e.route == "factcheck" and e.tier in TRUSTED_TIERS and e.reviewed_claim][: config.runtime.verify.judge_max_reviews]
    if not candidates:
        return out
    listing = "\n".join(f"{i}. {_clean(e.reviewed_claim)}" for i, e in enumerate(candidates, start=1))
    prompt = template.replace("{claim}", _clean(claim_text)).replace("{reviews}", listing)
    try:
        reply = client.generate_json(prompt, SCHEMA)
    except SourceError as exc:
        out.error = type(exc).__name__
        return out
    items = reply.data.get("items")
    if not isinstance(items, list):
        out.error = "BadOutput"
        return out
    for item in items:
        if isinstance(item, dict) and item.get("relation") in RELATIONS and isinstance(item.get("review"), int) and 1 <= item["review"] <= len(candidates):
            out.relations.setdefault(candidates[item["review"] - 1].passage_sha256, item["relation"])
    return out
