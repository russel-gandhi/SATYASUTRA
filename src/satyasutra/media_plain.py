"""The image check in plain words: one clear headline, what each check found, what the result does not say, and how to be sure.

It adds no new analysis. It reads the numbers the checks already produced, picks one of six outcomes (config/media_plain.yaml) and
fills in the wording. The "steps" are the real stages the check ran, in order, each with what it found: they are the reasoning of
the pipeline, not the hidden thinking of a language model (the image path calls no language model). A check that found nothing is
reported as "nothing found", never as "genuine".
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from .config import config_dir

FILE = "media_plain.yaml"
KINDS = ("clean", "edited", "ai_suspected", "both", "known_image", "unavailable")
DONE, FLAG, SKIP = "done", "flag", "skip"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Tool(_Strict):
    name: str
    url: str
    how: str


class DoNext(_Strict):
    intro: str
    tools: list[Tool]
    tips: list[str]


class Plain(_Strict):
    badges: dict[str, str]
    headlines: dict[str, str]
    meanings: dict[str, str]
    cannot_say: dict[str, list[str]]
    steps: dict
    do_next: DoNext

    @field_validator("badges", "headlines", "meanings", "cannot_say")
    @classmethod
    def _every_outcome_has_words(cls, v: dict) -> dict:
        missing = [k for k in KINDS if k not in v]
        if missing:
            raise ValueError(f"missing outcomes: {missing}")
        return v


def load_plain(path: Path | None = None) -> Plain:
    return Plain.model_validate(yaml.safe_load((path or config_dir() / FILE).read_text(encoding="utf-8")))


@cache
def _default() -> Plain:
    return load_plain()


def outcome_kind(*, available: bool, edited: bool, ai_flagged: bool, matched: bool) -> str:
    if edited and ai_flagged:
        return "both"
    if edited:
        return "edited"
    if ai_flagged:
        return "ai_suspected"
    if matched:
        return "known_image"
    return "clean" if available else "unavailable"


def build_summary(*, width: int, height: int, forensics: dict, matches: list[dict], edit_cut: float | None, ai_cut: float | None,
                  plain: Plain | None = None) -> dict:
    p = plain or _default()
    available = bool(forensics.get("available"))
    score, ai = forensics.get("score"), forensics.get("ai_score")
    edited = available and score is not None and edit_cut is not None and score >= edit_cut
    ai_flagged = available and ai is not None and ai_cut is not None and ai >= ai_cut
    kind = outcome_kind(available=available, edited=edited, ai_flagged=ai_flagged, matched=bool(matches))

    s = p.steps
    steps = [{"id": "read", "did": s["read"]["did"], "result": s["read"]["result"].format(width=width, height=height), "status": DONE}]
    steps.append({"id": "history", "did": s["history"]["did"], "status": FLAG if matches else DONE,
                  "result": s["history"]["result_some"].format(n=len(matches)) if matches else s["history"]["result_none"]})
    if score is None or edit_cut is None or not available:
        edit_step = (s["edit"]["result_none"], SKIP)
    elif edited:
        edit_step = (s["edit"]["result_flag"].format(score=f"{score:.2f}", cut=f"{edit_cut:g}"), FLAG)
    else:
        edit_step = (s["edit"]["result_clean"].format(score=f"{score:.2f}", cut=f"{edit_cut:g}"), DONE)
    steps.append({"id": "edit", "did": s["edit"]["did"], "result": edit_step[0], "status": edit_step[1]})
    if ai is None or ai_cut is None or not available:
        reason = f": {forensics['ai_error']}" if forensics.get("ai_error") else ""
        ai_step = (s["ai"]["result_none"].format(reason=reason), SKIP)
    elif ai_flagged:
        ai_step = (s["ai"]["result_flag"].format(score=f"{ai:.2f}", cut=f"{ai_cut:g}"), FLAG)
    else:
        ai_step = (s["ai"]["result_clean"].format(score=f"{ai:.2f}", cut=f"{ai_cut:g}"), DONE)
    steps.append({"id": "ai", "did": s["ai"]["did"], "result": ai_step[0], "status": ai_step[1]})
    steps.append({"id": "rule", "did": s["rule"]["did"], "result": s["rule"]["result"][kind], "status": DONE})

    return {
        "kind": kind, "badge": p.badges[kind], "headline": p.headlines[kind], "meaning": p.meanings[kind],
        "found": [st["result"] for st in steps if st["id"] in ("history", "edit", "ai")],
        "steps": steps, "cannot_say": p.cannot_say[kind],
        "do_next": {"intro": p.do_next.intro, "tools": [t.model_dump() for t in p.do_next.tools], "tips": p.do_next.tips},
    }
