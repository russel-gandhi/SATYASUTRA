"""Text to a canonical form, so reposts, quote-posts and lightly edited copies compare equal or close."""

from __future__ import annotations

import hashlib
import re
import unicodedata

URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
MENTION = re.compile(r"@[\w.\-]+")
DROPPED_CATEGORIES = ("P", "S", "C", "Z")      # punctuation, symbols (emoji), control/format, separators (spaces are re-added)


def normalise(text: str) -> str:
    """NFKC, case-folded, without links, mentions, punctuation or emoji; letters, digits and combining marks in any script stay."""
    text = unicodedata.normalize("NFKC", text)
    text = MENTION.sub(" ", URL.sub(" ", text)).casefold()
    kept = [ch if unicodedata.category(ch)[0] not in DROPPED_CATEGORIES else " " for ch in text]
    return " ".join("".join(kept).split())


def text_key(normalised: str) -> str:
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def shingles(normalised: str, k: int) -> set[str]:
    """Overlapping runs of k words. Text shorter than k words becomes one shingle, so it still has a signature."""
    words = normalised.split()
    if not words:
        return set()
    if len(words) < k:
        return {" ".join(words)}
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}
