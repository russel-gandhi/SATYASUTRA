"""The walking skeleton: one post in, cited claim verdicts out, one ledger row per verdict.

post -> claim extraction (Gemini) -> Fact Check lookup -> verdict rules -> ledger. No stage invents a label: when the
lookup finds nothing the rules return UNVERIFIABLE_YET, and when a stage fails the error propagates instead of a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .claims import ExtractionResult, Judgements
from .claims.rules import RuleOutcome, decide
from .config import Config
from .ledger import LedgerStore
from .schemas import Claim, Post
from .schemas.core import sha256_text
from .sources import FactCheckResult


@dataclass
class ClaimResult:
    claim: Claim
    outcome: RuleOutcome
    ledger_seq: int
    from_cache: bool


@dataclass
class VerifyOutcome:
    post: Post
    extraction: ExtractionResult
    results: list[ClaimResult] = field(default_factory=list)


def verify_post(
    post: Post,
    config: Config,
    ledger: LedgerStore,
    extract: Callable[[Post], ExtractionResult],
    lookup: Callable[[str, str | None], FactCheckResult],
    judge: Callable[[str, list], Judgements] | None = None,
) -> VerifyOutcome:
    extraction = extract(post)
    out = VerifyOutcome(post, extraction)
    for claim in extraction.claims:
        found = lookup(claim.text_en, claim.lang)
        judgement = judge(claim.text_en, found.evidence) if judge else None
        outcome = decide(claim, found.evidence, config, judged=judgement.relations if judgement else None)
        if judgement and judgement.error:
            outcome.reasons.append(f"same-claim judge unavailable ({judgement.error}): strict word-overlap rule used")
        entry = ledger.append(config.runtime.verify.ledger_kind, _payload(claim, outcome, extraction, judgement), _idem_key(claim, outcome))
        out.results.append(ClaimResult(claim, outcome, entry.seq, found.from_cache))
    return out


def _evidence_ids(outcome: RuleOutcome) -> list[str]:
    return sorted(e.passage_sha256 for e in outcome.verdict.evidence)


def _idem_key(claim: Claim, outcome: RuleOutcome) -> str:
    """The same claim text with the same evidence is the same decision, so a repeat writes nothing new."""
    return sha256_text("|".join([sha256_text(claim.text_en), outcome.verdict.label, *_evidence_ids(outcome)]))


def _payload(claim: Claim, outcome: RuleOutcome, extraction: ExtractionResult, judgement: Judgements | None = None) -> dict:
    v = outcome.verdict
    return {
        "verdict_id": v.id,
        "claim_id": claim.id,
        "post_id": claim.post_id,
        "claim_sha256": sha256_text(claim.text_en),
        "label": v.label,
        "confidence_band": v.confidence_band,
        "flags": v.flags,
        "evidence": [{"passage_sha256": e.passage_sha256, "url": e.url, "role": e.role, "tier": e.tier, "publisher": e.publisher} for e in v.evidence],
        "reasons": outcome.reasons,
        "extraction": {"model": extraction.model, "prompt_sha256": extraction.prompt_sha256},
        "judge": {"model": judgement.model, "prompt_sha256": judgement.prompt_sha256, "relations": len(judgement.relations), "error": judgement.error} if judgement else None,
    }
