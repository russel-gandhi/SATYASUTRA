"""Contrast refutation: a claim's number against the same number in an official passage.

Gemini only points at the two texts: is this the same subject, is it the same measured quantity, and which exact words in the claim
and in the passage carry the number. Everything that decides is plain code, so the model cannot invent a contradiction:
  - both quoted spans must appear verbatim in the claim and in the passage (an invented span fires nothing),
  - both must parse as numbers, and only numbers that differ beyond the rounding tolerance count,
  - a matching number, a missing quantity or an unsure answer produce no evidence at all.
A contradiction becomes Tier-of-the-passage evidence with route "contrast"; the rules turn it into REFUTED. Only numbers are
compared: dates and names differ in too many spellings to compare safely.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..config import Config
from ..errors import SourceError
from ..llm import GeminiClient
from ..schemas import Evidence
from ..schemas.core import sha256_text
from ..sources.index import OfficialIndex, Passage
from ..sources.official import refresh_if_stale
from .extract import load_prompt

PROMPT = "contrast.txt"
CLAIM_OPEN, CLAIM_CLOSE, PASSAGES_OPEN, PASSAGES_CLOSE = "<<<CLAIM", "CLAIM>>>", "<<<PASSAGES", "PASSAGES>>>"
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
MULTIPLIERS = {"thousand": 1e3, "k": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "million": 1e6, "mn": 1e6, "crore": 1e7, "crores": 1e7, "billion": 1e9, "bn": 1e9, "trillion": 1e12}

SCHEMA = {
    "type": "OBJECT",
    "properties": {"items": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "passage": {"type": "INTEGER"}, "same_subject": {"type": "BOOLEAN"}, "same_quantity": {"type": "BOOLEAN"},
        "claim_value": {"type": "STRING"}, "official_value": {"type": "STRING"}},
        "required": ["passage", "same_subject", "same_quantity", "claim_value", "official_value"]}}},
    "required": ["items"],
}


def parse_number(text: str) -> float | None:
    """'₹2,000', '6.5%', '2 lakh', '5 crore rupees' -> a float; None when there is not exactly one number."""
    found = NUMBER.findall(text)
    if len(found) != 1:
        return None
    value = float(found[0].replace(",", ""))
    words = re.findall(r"[a-z]+", text.casefold())
    for w in words:
        if w in MULTIPLIERS:
            return value * MULTIPLIERS[w]
    return value


def _clean(text: str) -> str:
    for marker in (CLAIM_OPEN, CLAIM_CLOSE, PASSAGES_OPEN, PASSAGES_CLOSE):
        text = text.replace(marker, "")
    return " ".join(text.split())


def _contains(text: str, span: str) -> bool:
    return bool(span.strip()) and " ".join(span.split()).casefold() in " ".join(text.split()).casefold()


def _appears(value: str, text: str, tol: float) -> bool:
    """The claim's own figure occurs somewhere in the passage. A table row holds many numbers, so a different figure next to the quoted one proves nothing."""
    mine = NUMBER.findall(value)
    if len(mine) != 1:
        return False
    x = float(mine[0].replace(",", ""))
    return any(math.isclose(x, float(n.replace(",", "")), rel_tol=tol) for n in NUMBER.findall(text))


def _sentence_with(text: str, span: str) -> str:
    spans = re.split(r"(?<=[.!?])\s+", text)
    return next((s for s in spans if _contains(s, span)), text)


@dataclass
class OfficialResult:
    passages: list[Passage] = field(default_factory=list)
    contrast: list[Evidence] = field(default_factory=list)       # contradictions only
    checked: list[dict] = field(default_factory=list)            # one line per passage, why it did or did not fire
    refresh: dict[str, str] | None = None
    error: str = ""


def contrast_check(client: GeminiClient, cfg: Config, claim_text: str, passages: list[Passage]) -> OfficialResult:
    out = OfficialResult(passages=passages)
    if not passages:
        return out
    template = load_prompt(PROMPT)
    listing = "\n".join(f"{i}. {_clean(p.text)}" for i, p in enumerate(passages, start=1))
    prompt = template.replace("{claim}", _clean(claim_text)).replace("{passages}", listing)
    try:
        reply = client.generate_json(prompt, SCHEMA)
    except SourceError as exc:
        out.error = type(exc).__name__
        return out
    items = reply.data.get("items")
    if not isinstance(items, list):
        out.error = "BadOutput"
        return out
    tol = cfg.runtime.official.contrast_rel_tol
    for item in items:
        if not (isinstance(item, dict) and isinstance(item.get("passage"), int) and 1 <= item["passage"] <= len(passages)):
            continue
        p = passages[item["passage"] - 1]
        cv, ov = str(item.get("claim_value", "")), str(item.get("official_value", ""))
        note = {"url": p.url, "claim_value": cv, "official_value": ov}
        if not (item.get("same_subject") is True and item.get("same_quantity") is True):
            out.checked.append(note | {"fired": False, "why": "not the same subject and quantity"})
        elif not (_contains(claim_text, cv) and _contains(p.text, ov)):
            out.checked.append(note | {"fired": False, "why": "a quoted value is not in the text it came from"})
        else:
            a, b = parse_number(cv), parse_number(ov)
            if a is None or b is None:
                out.checked.append(note | {"fired": False, "why": "a value is not a single number"})
            elif math.isclose(a, b, rel_tol=tol):
                out.checked.append(note | {"fired": False, "why": "the numbers match"})
            elif _appears(cv, p.text, tol):
                out.checked.append(note | {"fired": False, "why": "the claim's number is also in the passage (another cell of the same text): not a contradiction"})
            else:
                sentence = _sentence_with(p.text, ov)
                out.checked.append(note | {"fired": True, "why": f"claim says {cv}, the official text says {ov}"})
                out.contrast.append(Evidence(
                    role="contradicts", route="contrast", source_id=p.source_id, url=p.url, fetched_at=p.fetched_at, tier=p.tier,  # type: ignore[arg-type]
                    passage=p.text, passage_sha256=p.sha256, model=client.model, model_sha256=sha256_text(template),
                    contrast_claim=sentence, publisher=p.publisher, reviewed_claim=cv, reviewed_at=p.published_at))
    return out


def check_official(client: GeminiClient, cfg: Config, index: OfficialIndex, claim_text: str, http=None) -> OfficialResult:
    """Refresh the index when stale, retrieve the best passages for the claim, run the contrast check."""
    s = cfg.runtime.official
    refreshed = refresh_if_stale(cfg, index, http)
    passages = index.search(claim_text, s.max_results, s.min_token_chars)
    out = contrast_check(client, cfg, claim_text, passages)
    out.refresh = refreshed
    return out
