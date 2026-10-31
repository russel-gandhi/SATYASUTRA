"""Correction cards: the words of a shareable card for a decided verdict, in English or Hindi.

Only the text is built here (and validated as a CorrectionCard: https links only, bounded lengths); the page draws it, because the server's
Pillow cannot shape Devanagari. A card is made only for REFUTED, SUPPORTED or MISSING_CONTEXT at medium or high confidence, takes the
language of the original claim, and carries a source link but never the original media. Not decided means no card.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from .config import config_dir
from .schemas import CorrectionCard

FILE = "cards.yaml"
HINDI = "hi"
DEFAULT_LANG = "en"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Eligible(_Strict):
    labels: list[str]
    bands: list[str]
    quote_max_chars: int


class Cards(_Strict):
    eligible: Eligible
    languages: dict
    colors: dict[str, str]


def load_cards(path: Path | None = None) -> Cards:
    return Cards.model_validate(yaml.safe_load((path or config_dir() / FILE).read_text(encoding="utf-8")))


def card_language(lang: str) -> str:
    return HINDI if (lang or "").casefold().startswith(HINDI) else DEFAULT_LANG


def build_card(claim: dict, cards: Cards, now: datetime | None = None) -> dict | None:
    """The card for one claim from /verify, or None when the claim is not eligible (undecided, or low confidence)."""
    e = cards.eligible
    label, band = claim.get("label"), claim.get("confidence_band")
    if label not in e.labels or band not in e.bands:
        return None
    cites = [c for c in claim.get("citations", []) if str(c.get("url", "")).startswith("https://")]  # forge: allow-literal scheme check, not a source
    if not cites:
        return None
    lang = card_language(claim.get("lang", ""))
    t = cards.languages[lang]
    first = cites[0]
    publisher = first.get("publisher") or "A fact-checker"
    rating = first.get("rating") or ""
    sentence = (t["sentence"].format(publisher=publisher, rating=rating) if rating
                else t["sentence_no_rating"].format(publisher=publisher, verdict=cards.languages["verdict_words"][lang][label]))
    quote = (claim.get("quote") if lang == HINDI and claim.get("quote") else claim.get("text")) or ""
    card = CorrectionCard(verdict_id=str(claim.get("claim_id", "")), lang=lang, headline=t["headline"][label], sentence=sentence[:400],
                          links=[c["url"] for c in cites[:2]], checked_at=now or datetime.now(timezone.utc))
    return {**card.model_dump(mode="json"), "claim": quote[:e.quote_max_chars], "label": label, "color": cards.colors[label],
            "claim_label": t["claim_label"], "source_label": t["source_label"], "footer": t["footer"],
            "ledger": t["ledger_label"].format(seq=claim.get("ledger_seq")) if claim.get("ledger_seq") is not None else "",
            "filename": t["filename"], "publisher": publisher}
