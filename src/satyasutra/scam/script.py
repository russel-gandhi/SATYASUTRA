"""Scam Guard rules (package SG1): which signals a stretch of call words contains, and what state a rolling window of chunks adds up to.

Plain functions over config/scam_script.yaml, no model and no network. A sentence that carries a negation phrase ("never share your OTP", what a real
bank says) cancels the credential, money and remote-access signals in that sentence, so a genuine warning does not alert. The page never says a call is
safe: the calmest state is "nothing alarming heard so far".
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from ..config import config_dir

FILE = "scam_script.yaml"
SENTENCE_BREAK = re.compile(r"[.!?\n।;]+")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Text(_Strict):
    en: str
    hi: str


class Settings(_Strict):
    window_chunks: int
    min_signals_amber: int
    session_s: float
    max_sessions: int
    max_chunk_bytes: int
    max_text_chars: int
    tail_chars: int


class ScamScript(_Strict):
    reviewed_by: str
    reviewed_on: str
    settings: Settings
    signals: dict[str, list[str]]
    negations: list[str]
    negation_cancels: list[str]
    red_when: list[list[str]]
    amber_on: list[str]
    types: dict[str, list[str]]
    states: dict[str, Text]
    advice: dict[str, Text]


def load_script(path: Path | None = None) -> ScamScript:
    return ScamScript.model_validate(yaml.safe_load((path or config_dir() / FILE).read_text(encoding="utf-8")))


def _has(sentence: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(phrase.casefold()) + r"(?!\w)", sentence) is not None


def signals_in(text: str, rules: ScamScript) -> set[str]:
    """The signals present in this text, sentence by sentence, with negated credential, money and remote-access signals removed."""
    found: set[str] = set()
    for sentence in SENTENCE_BREAK.split(text.casefold()):
        if not sentence.strip():
            continue
        negated = any(_has(sentence, n) for n in rules.negations)
        for name, phrases in rules.signals.items():
            if negated and name in rules.negation_cancels:
                continue
            if any(_has(sentence, p) for p in phrases):
                found.add(name)
    return found


@dataclass
class Verdict:
    state: str                  # listening, careful or scam
    signals: list[str]          # what is present in the window
    scam_type: str = ""


def judge(window: set[str], rules: ScamScript) -> Verdict:
    ordered = sorted(window)
    if any(set(need) <= window for need in rules.red_when):
        kind = next((name for name, need in rules.types.items() if set(need) <= window), "")
        return Verdict("scam", ordered, kind)
    if len(window) >= rules.settings.min_signals_amber or window & set(rules.amber_on):
        return Verdict("careful", ordered)
    return Verdict("listening", ordered)


class Call:
    """One call's rolling window of per-chunk signal sets. Lives in memory only."""

    def __init__(self, rules: ScamScript):
        self.rules = rules
        self.chunks: deque[set[str]] = deque(maxlen=rules.settings.window_chunks)
        self.tail = ""

    def add(self, text: str) -> Verdict:
        self.chunks.append(signals_in(text, self.rules))
        self.tail = " ".join((self.tail + " " + text).split())[-self.rules.settings.tail_chars:]
        return judge(set().union(*self.chunks), self.rules)
