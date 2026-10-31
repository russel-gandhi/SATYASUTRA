"""Post text to atomic claims, using Gemini with a response schema.

The model proposes; plain code disposes. Every quote must appear verbatim in the post (that is how the span is computed),
every language code must look like one, and anything else is rejected, retried once, and then reported as invalid. Nothing is
repaired by guessing. Non-claims are returned too, with their kind, so nothing is dropped silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence

from ..config import Config, config_dir
from ..errors import ContentBlocked, SourceError
from ..llm import BadOutput, GeminiClient
from ..schemas import Claim, Post
from ..schemas.core import sha256_text

KINDS = ("factual_claim", "opinion", "question", "personal_experience", "other")
LANG = re.compile(r"^[a-z]{2,3}$")
POST_OPEN, POST_CLOSE = "<<<POST", "POST>>>"
ENGLISH = "en"

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "quote": {"type": "STRING"},
                    "claim_en": {"type": "STRING"},
                    "lang": {"type": "STRING"},
                    "kind": {"type": "STRING", "enum": list(KINDS)},
                    "checkworthy": {"type": "BOOLEAN"},
                    "time_ref": {"type": "STRING"},
                    "place_ref": {"type": "STRING"},
                },
                "required": ["quote", "claim_en", "lang", "kind", "checkworthy"],
            },
        }
    },
    "required": ["items"],
}


@dataclass
class NonClaim:
    quote: str
    paraphrase_en: str
    kind: str                      # opinion, question, personal_experience, other, or "not_checkworthy" for a factual claim not worth checking
    lang: str


@dataclass
class ExtractionResult:
    status: Literal["ok", "no_items", "invalid_output", "blocked", "failed"]
    claims: list[Claim] = field(default_factory=list)
    non_claims: list[NonClaim] = field(default_factory=list)
    reason: str = ""
    attempts: int = 0
    model: str = ""
    prompt_sha256: str = ""
    tokens: int | None = None
    promoted: list[str] = field(default_factory=list)      # quotes kept as claims by the high-stakes safety net


class Invalid(Exception):
    """The model's output broke a rule. The message says which."""


def load_prompt(name: str = "extract_claims.txt") -> str:
    return (config_dir() / "prompts" / name).read_text(encoding="utf-8")


def build_prompt(template: str, post_text: str) -> str:
    cleaned = post_text.replace(POST_OPEN, "").replace(POST_CLOSE, "")     # the post cannot close its own delimiter
    return template.replace("{post_text}", cleaned)


def _mentions(text: str, terms: Sequence[str]) -> bool:
    """True when `text` contains one of the terms: whole words for Latin-script terms, plain substrings for other scripts."""
    low = text.casefold()
    for term in terms:
        term = term.casefold()
        if term.isascii():
            if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", low):
                return True
        elif term in low:
            return True
    return False


def validate_items(post: Post, data: dict, max_items: int, model: str, promote_terms: Sequence[str] = (), promote_kinds: Sequence[str] = (),
                   promoted: list[str] | None = None) -> tuple[list[Claim], list[NonClaim]]:
    """Check every item the model returned. `promote_terms` and `promote_kinds` are a safety net: a short statement the model filed as a question,
    "other" or "not worth checking" is kept as a claim when its own words (or the model's restatement) contain a high-stakes term such as dead or
    arrested. Opinions and personal experiences are never promoted. The model's restatement is used as it is; the quotes of promoted items go
    into `promoted` so the result can say so."""
    items = data.get("items")
    if not isinstance(items, list):
        raise Invalid("'items' is not a list")
    if len(items) > max_items:
        raise Invalid(f"{len(items)} items, more than the limit of {max_items}")
    claims: list[Claim] = []
    others: list[NonClaim] = []
    cursor = 0
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise Invalid(f"item {i} is not an object")
        quote, claim_en, lang, kind, worth = (item.get(k) for k in ("quote", "claim_en", "lang", "kind", "checkworthy"))
        if not (isinstance(quote, str) and quote.strip() and isinstance(claim_en, str) and claim_en.strip()):
            raise Invalid(f"item {i} has an empty quote or claim_en")
        if not (isinstance(lang, str) and LANG.match(lang)):
            raise Invalid(f"item {i} has a bad language code")
        if kind not in KINDS or not isinstance(worth, bool):
            raise Invalid(f"item {i} has a bad kind or checkworthy flag")
        start = post.text.find(quote, cursor)
        if start < 0:
            start = post.text.find(quote)          # out of order is allowed; the quote still has to be in the post
        if start < 0:
            raise Invalid(f"item {i}: the quote does not appear verbatim in the post")
        cursor = start + len(quote)
        promotable = (kind == "factual_claim" and not worth) or kind in promote_kinds
        keep = kind == "factual_claim" and worth
        if promotable and (_mentions(quote, promote_terms) or _mentions(claim_en, promote_terms)):
            keep = True
            if promoted is not None:
                promoted.append(quote)
        if not keep and kind != "factual_claim":
            others.append(NonClaim(quote, claim_en, kind, lang))
        elif not keep:
            others.append(NonClaim(quote, claim_en, "not_checkworthy", lang))
        else:
            span = (start, start + len(quote))
            claims.append(Claim(
                id=sha256_text(f"{post.id}|{span[0]}|{span[1]}|{claim_en}")[:16],
                post_id=post.id,
                text_en=claim_en.strip(),
                text_orig=quote,
                span=span,
                lang=lang,
                translated_by=None if lang == ENGLISH else model,
                time_ref=(item.get("time_ref") or "").strip() or None,
                place_ref=(item.get("place_ref") or "").strip() or None,
            ))
    return claims, others


def extract_claims(client: GeminiClient, config: Config, post: Post, template: str | None = None) -> ExtractionResult:
    template = template if template is not None else load_prompt()
    prompt = build_prompt(template, post.text)
    result = ExtractionResult("failed", model=client.model, prompt_sha256=sha256_text(template))
    max_items = config.runtime.extract.max_claims_per_post
    last = ""
    for attempt in (1, 2):                      # one retry for output that breaks a rule; transport errors are not retried here
        result.attempts = attempt
        try:
            reply = client.generate_json(prompt, SCHEMA)
        except ContentBlocked as exc:
            result.status, result.reason = "blocked", exc.detail
            return result
        except BadOutput as exc:
            last = exc.detail
            continue
        except SourceError as exc:
            result.status, result.reason = "failed", f"{type(exc).__name__}: {exc.detail}"
            return result
        result.tokens = reply.tokens
        try:
            result.promoted = []
            ex = config.runtime.extract
            result.claims, result.non_claims = validate_items(post, reply.data, max_items, reply.model, ex.promote_terms, ex.promote_kinds, result.promoted)
        except Invalid as exc:
            last = str(exc)
            continue
        result.status = "ok" if (result.claims or result.non_claims) else "no_items"
        return result
    result.status, result.reason = "invalid_output", last
    result.claims, result.non_claims = [], []
    return result
