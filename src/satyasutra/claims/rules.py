"""Verdict rules v0: plain code, no model. Evidence in, label out, with the reason for every decision written down.

What this version can and cannot do (measured on live Fact Check data, see config/thresholds.yaml):
  - A fact-check is a review of *some* claim. The API returns reviews of similar claims, and often of the OPPOSITE claim
    ("Smoking doesn't cause cancer: False" comes back for "Smoking causes lung cancer"). Word overlap cannot tell them apart.
  - So a review decides only when its claim is near-identical to ours and the negation matches. Everything else is returned
    as related context and never decides. Semantic matching waits for embeddings and NLI (Phase 3).
Only Tier 1 or 2 evidence can decide; absence of support never refutes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

from ..config import Config
from ..errors import ForgeError
from ..fastclock.normalise import normalise
from ..schemas import Claim, Evidence, Verdict
from ..schemas.core import TRUSTED_TIERS, sha256_text

Stance = Literal["refutes", "supports", "mixed", "unproven", "unmapped"]
WORD = re.compile(r"[a-z']+")


def relevance(claim_text: str, reviewed_claim: str) -> float:
    """How much two claims overlap, as Jaccard similarity of their normalised words (0 to 1).
    Crude on purpose, and unable to tell a claim from its opposite; see the module note."""
    a, b = set(normalise(claim_text).split()), set(normalise(reviewed_claim).split())
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def is_negated(text: str, negations: list[str]) -> bool:
    """True when the text contains a negation word or a contraction like "doesn't"."""
    words = WORD.findall(text.casefold().replace("’", "'"))
    plain = {n for n in negations if not n.startswith("n'")}
    suffixes = tuple(n for n in negations if n.startswith("n'"))
    return any(w in plain or (suffixes and w.endswith(suffixes)) for w in words)


def classify_rating(rating: str, config: Config) -> Stance:
    """Map a publisher's rating to a stance by the longest matching leading phrase; no match is "unmapped"."""
    tokens = normalise(rating).split()
    best_len, best = 0, None
    for group, phrases in config.policy.verdict_rules.rating_map.model_dump().items():
        for phrase in phrases:
            p = normalise(phrase).split()
            if p and tokens[:len(p)] == p and len(p) > best_len:
                best_len, best = len(p), group
    return best or "unmapped"


@dataclass
class Assessment:
    evidence: Evidence
    relevance: float
    stance: Stance
    used: bool
    reason: str


@dataclass
class RuleOutcome:
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)       # one line per decision, in the order the rules ran
    related: list[Assessment] = field(default_factory=list)  # reviews of other claims: shown to people, never used to decide
    unmapped_ratings: list[str] = field(default_factory=list)


def decide(
    claim: Claim,
    evidence: list[Evidence],
    config: Config,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    judged: dict[str, str] | None = None,
) -> RuleOutcome:
    """`judged` maps a review's passage hash to the same-claim judge's relation (same, opposite or different).
    A review the judge covered is decided by that relation; any other review keeps the strict word-overlap test."""
    judged = judged or {}
    rules = config.policy.verdict_rules
    cut = config.thresholds.thresholds["factcheck_relevance"].value
    assessments: list[Assessment] = []

    for ev in evidence:
        rel = relevance(claim.text_en, ev.reviewed_claim) if ev.reviewed_claim else 0.0
        stance = classify_rating(ev.rating, config) if ev.rating else "unmapped"
        if ev.route == "contrast":
            ok = ev.tier in TRUSTED_TIERS and ev.role == "contradicts"
            assessments.append(Assessment(ev, 1.0, "refutes" if ok else "unmapped", ok,
                                          f"official text from {ev.publisher or ev.source_id} gives a different number: {ev.contrast_claim}" if ok
                                          else f"contrast evidence from tier {ev.tier} cannot decide"))
        elif ev.route != "factcheck":
            assessments.append(Assessment(ev, rel, stance, False, f"route {ev.route!r} is not handled by v0 rules"))
        elif ev.tier not in TRUSTED_TIERS:
            assessments.append(Assessment(ev, rel, stance, False, f"tier {ev.tier} cannot decide (only tier 1 or 2)"))
        elif ev.passage_sha256 in judged:
            relation = judged[ev.passage_sha256]
            if relation == "same":
                assessments.append(Assessment(ev, rel, stance, True, f"judged the same claim, rated {ev.rating!r} -> {stance}"))
            else:
                assessments.append(Assessment(ev, rel, stance, False, f"judged to review {'the opposite' if relation == 'opposite' else 'a different'} claim"))
        elif rel < cut:
            assessments.append(Assessment(ev, rel, stance, False, f"reviews a different claim (relevance {rel:.2f} below {cut})"))
        elif is_negated(claim.text_en, rules.negations) != is_negated(ev.reviewed_claim, rules.negations):
            assessments.append(Assessment(ev, rel, stance, False, "may review the opposite claim (negation differs)"))
        else:
            assessments.append(Assessment(ev, rel, stance, True, f"same claim (relevance {rel:.2f}), rated {ev.rating!r} -> {stance}"))

    used = [a for a in assessments if a.used]
    related = sorted((a for a in assessments if not a.used), key=lambda a: a.relevance, reverse=True)[: rules.related_limit]
    reasons = [a.reason for a in assessments]
    unmapped = sorted({a.evidence.rating for a in used if a.stance == "unmapped"})

    refutes = [a for a in used if a.stance == "refutes"]
    supports = [a for a in used if a.stance == "supports"]
    mixed = [a for a in used if a.stance == "mixed"]
    flags: list[str] = []
    cited: list[Evidence] = []
    label, deciders = "UNVERIFIABLE_YET", []

    if supports and refutes:                                    # C6 step 4: show both sides, decide neither
        flags.append("CONFLICT")
        cited = [_role(a, "supports") for a in supports] + [_role(a, "contradicts") for a in refutes]
        reasons.append("trusted reviews disagree: no label, both sides shown")
    elif supports and mixed:                                    # C6 step 5
        label, deciders = "MISSING_CONTEXT", supports
        cited = [_role(a, "supports") for a in supports] + [_role(a, "context") for a in mixed]
        reasons.append("support exists but a trusted review says context is missing")
    elif supports:
        label, deciders = "SUPPORTED", supports
        cited = [_role(a, "supports") for a in supports]
        reasons.append("trusted review of the same claim rates it true")
    elif refutes:
        label, deciders = "REFUTED", refutes
        cited = [_role(a, "contradicts") for a in refutes]
        reasons.append("trusted source contradicts the claim")
    else:
        if used:
            cited = [_role(a, "context") for a in used]
            reasons.append("same-claim reviews exist but none is a decisive rating (mixed, unproven or unmapped)")
        else:
            reasons.append("no trusted review of this claim")

    if unmapped:
        reasons.append(f"unmapped ratings stay visible and decide nothing: {unmapped}")
    if claim.translated_by:
        flags.append("TRANSLATED")
    if deciders and any(a.evidence.reviewed_at and a.evidence.reviewed_at < now() - timedelta(days=rules.stale_after_days) for a in deciders):
        flags.append("STALE_SOURCE")

    publishers = {a.evidence.publisher for a in deciders}
    band = "high" if len(publishers) >= rules.min_publishers_for_high else ("medium" if deciders else "low")

    verdict = Verdict(
        id=sha256_text("|".join([claim.id, label, *sorted(e.passage_sha256 for e in cited)]))[:16],
        target_id=claim.id, axis="claim", label=label, confidence_band=band, evidence=cited, flags=flags,
    )
    if verdict.label != label or "COERCED" in verdict.flags:
        raise ForgeError(f"rules chose {label} but the schema had to coerce it: the rules and the contract disagree")
    return RuleOutcome(verdict, reasons, related, unmapped)


def _role(a: Assessment, role: str) -> Evidence:
    return a.evidence.model_copy(update={"role": role})
