"""Explanations of a verdict in the SHAP style: how much each piece of evidence or each detector contributed to the outcome.

The decision rules are plain code, so the attribution is exact and needs no model: the Shapley value of a player is its average marginal
effect on the outcome over every order in which the players could be added. Here the outcome is "the rules still give the final label"
(1) or not (0). A positive value means the item pushed towards the label, zero means removing it changes nothing, and a negative value
means it pulled away. Only the rule stage is attributed: claim extraction, translation and the same-claim judgement are model outputs
that arrive as inputs to the rules, and the explanation says so. Nothing here changes a verdict.
"""

from __future__ import annotations

from itertools import combinations
from math import factorial
from typing import Callable

from .config import Config
from .fastclock.normalise import normalise

NOT_ATTRIBUTED = ("Claim extraction, translation and the same-claim judgement are done by a language model; they are inputs to these rules "
                  "and are not attributed here. Their output is in the ledger entry.")


def shapley(players: list, outcome: Callable[[list], int]) -> list[float]:
    """Exact Shapley values of `players` for a 0/1 `outcome` of any subset (kept small by the caller: the work doubles per player)."""
    n = len(players)
    values = [0.0] * n
    for i in range(n):
        others = [j for j in range(n) if j != i]
        for size in range(len(others) + 1):
            weight = factorial(size) * factorial(n - size - 1) / factorial(n)
            for subset in combinations(others, size):
                without = outcome([players[j] for j in subset])
                with_i = outcome([players[j] for j in sorted((*subset, i))])
                values[i] += weight * (with_i - without)
    return values


def word_overlap(claim_words: set[str], review_words: set[str], shown: int) -> dict:
    return {"shared": sorted(claim_words & review_words)[:shown], "only_in_claim": sorted(claim_words - review_words)[:shown],
            "only_in_review": sorted(review_words - claim_words)[:shown]}


def counterfactuals(label: str, flags: list[str]) -> list[str]:
    """What new evidence would change the label under the current rules (read from the rule table, not guessed from the model)."""
    if "CONFLICT" in flags:
        return ["Trusted reviews disagree. A third trusted review is not a tie-breaker under these rules: both sides stay shown."]
    if label == "UNVERIFIABLE_YET":
        return ["A tier 1 or 2 fact-check of the same claim rated false would make this REFUTED.",
                "One rated true would make it SUPPORTED.",
                "Official text (for example an RBI notice) that gives a different number for the same quantity would make it REFUTED."]
    if label == "REFUTED":
        return ["A tier 1 or 2 review of the same claim rated true would produce a CONFLICT: no label, both sides shown."]
    if label == "SUPPORTED":
        return ["A tier 1 or 2 review of the same claim rated false would produce a CONFLICT: no label, both sides shown.",
                "A trusted review saying context is missing would make this MISSING_CONTEXT."]
    return []


def explain_claim(claim, evidence: list, outcome, cfg: Config, rerun: Callable[[list], str]) -> dict:
    """`rerun(subset)` returns the label the rules give for that subset of the evidence."""
    s = cfg.runtime.explain
    final = outcome.verdict.label
    rows = []
    attributed = len(evidence) <= s.max_players
    values = shapley(evidence, lambda sub: int(rerun(sub) == final)) if attributed and evidence else [0.0] * len(evidence)
    by_hash = {(a.evidence.passage_sha256, a.evidence.url): a for a in [*outcome.related]}
    for i, ev in enumerate(evidence):
        rest = evidence[:i] + evidence[i + 1:]
        rows.append({
            "publisher": ev.publisher, "url": ev.url, "rating": ev.rating, "route": ev.route, "tier": ev.tier,
            "decided": ev.passage_sha256 in {c.passage_sha256 for c in outcome.verdict.evidence},
            "shapley": round(values[i], 3) if attributed else None,
            "flips_verdict_if_removed": rerun(rest) != final,
            "words": word_overlap(set(normalise(claim.text_en).split()), set(normalise(ev.reviewed_claim).split()), s.words_shown) if ev.reviewed_claim else None,
            "why_not_used": by_hash[(ev.passage_sha256, ev.url)].reason if (ev.passage_sha256, ev.url) in by_hash else None,
        })
    return {
        "method": ("Exact Shapley values over the evidence items: each value is that item's average effect on whether the rules still give "
                   f"{final}. " if attributed else f"More than {s.max_players} evidence items, so only the leave-one-out test is shown. ")
        + "Positive pushes towards the label, zero changes nothing.",
        "final_label": final, "rule_trail": outcome.reasons, "evidence": rows,
        "counterfactuals": counterfactuals(final, outcome.verdict.flags), "not_attributed": NOT_ATTRIBUTED,
    }


def explain_media(cfg: Config, forensics: dict, matches: list, label: str, label_from: Callable[[bool, bool], str]) -> dict:
    """Per detector: score, cut, distance from the cut, and its Shapley value for the final label. `label_from(edited, ai)` is the label rule."""
    f = cfg.runtime.forensics
    signals = []
    flags = []
    for key, name, threshold_name, text in (("score", "TruFor (edits)", f.threshold, "an editing / splicing signal"),
                                            ("ai_score", "Smaug (AI-generated)", f.ai_threshold, "an AI-generation signal")):
        value, entry = forensics.get(key), cfg.thresholds.thresholds.get(threshold_name)
        hit = value is not None and entry is not None and value >= entry.value
        flags.append(hit)
        signals.append({"signal": name, "score": value, "cut": entry.value if entry else None,
                        "margin": round(value - entry.value, 3) if value is not None and entry else None, "over_cut": hit,
                        "meaning": text, "cut_basis": entry.calibrated_on if entry else None})
    final = label_from(*flags)
    players = [0, 1]

    def outcome(sub: list) -> int:
        return int(label_from(0 in sub and flags[0], 1 in sub and flags[1]) == final)

    for sig, v in zip(signals, shapley(players, outcome)):
        sig["shapley"] = round(v, 3)
    signals.append({"signal": "Debunked-image index", "matches": len(matches), "shapley": 0.0,
                    "meaning": "a lead about the image's history; it never changes the label"})
    return {"method": f"Exact Shapley values over the two detectors for the label {final}; a score close to its cut (small margin) is a fragile call.",
            "final_label": final, "signals": signals,
            "pixel_level": "The heatmap (when shown) marks where TruFor sees the editing signal; it is the pixel-level part of this explanation."}
