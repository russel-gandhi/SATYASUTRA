"""The guided flow as a small state machine over config/raksha.yaml (package RK3) and the report summary (RK4).

State lives in a Session object the caller keeps in memory; nothing here reads or writes a file except the config. The summary is a template
filled from the typed fields only: no model call, so nothing outside what the person typed (and the fixed labels) can appear in it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from ..act.plan import Actions
from ..config import config_dir

FILE = "raksha.yaml"
LANGS = ("en", "hi")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Text(_Strict):
    en: str
    hi: str


class Option(_Strict):
    label: Text
    next: str
    set: dict[str, str] = {}


class Field_(_Strict):
    key: str
    prompt: Text


class Step(_Strict):
    ask: Text | None = None
    options: list[Option] = []
    fields: list[Field_] = []
    next: str = ""
    final: bool = False
    say: str = ""
    summary: bool = False
    routes: list[str] = []
    routes_by_age: dict[str, list[str]] = {}


class Line(_Strict):
    key: str
    label: Text
    values: dict[str, Text] = {}


class SummaryTemplate(_Strict):
    title: Text
    made_on: Text
    lines: list[Line]


class Settings(_Strict):
    max_field_chars: int
    session_s: float


class Flow(_Strict):
    reviewed_by: str
    reviewed_on: str
    settings: Settings
    start: str
    messages: dict[str, Text]
    steps: dict[str, Step]
    summary_template: SummaryTemplate


def load_flow(path: Path | None = None) -> Flow:
    return Flow.model_validate(yaml.safe_load((path or config_dir() / FILE).read_text(encoding="utf-8")))


@dataclass
class Session:
    lang: str
    step: str
    answers: dict[str, str] = field(default_factory=dict)
    field_index: int = 0
    done: bool = False
    expires: float = 0.0


def begin(flow: Flow, lang: str, now: float | None = None) -> tuple[Session, str]:
    lang = lang if lang in LANGS else "en"
    s = Session(lang=lang, step=flow.start, expires=(now if now is not None else time.monotonic()) + flow.settings.session_s)
    return s, flow.messages["intro"].model_dump()[lang] + "\n\n" + _prompt(flow, s)


def _prompt(flow: Flow, s: Session) -> str:
    step = flow.steps[s.step]
    if step.fields:
        field_ = step.fields[s.field_index]
        head = step.ask.model_dump()[s.lang] + "\n\n" if step.ask and s.field_index == 0 else ""
        return head + field_.prompt.model_dump()[s.lang] + "\n" + flow.messages["skip"].model_dump()[s.lang]
    lines = [step.ask.model_dump()[s.lang]] if step.ask else []
    lines += [f"{i}. {o.label.model_dump()[s.lang]}" for i, o in enumerate(step.options, start=1)]
    return "\n".join(lines)


def summary(flow: Flow, s: Session, today: date) -> str:
    """Typed fields in, dated text out. A value is cut to max_field_chars; a fixed label is used for the choice answers."""
    t, lang, out = flow.summary_template, s.lang, []
    out.append(f"{t.title.model_dump()[lang]}\n{t.made_on.model_dump()[lang]} {today.isoformat()}")
    for line in t.lines:
        raw = s.answers.get(line.key, "")
        if not raw or raw == "-":
            continue
        shown = line.values[raw].model_dump()[lang] if line.values else " ".join(raw.split())[: flow.settings.max_field_chars]
        out.append(f"{line.label.model_dump()[lang]}: {shown}")
    return "\n".join(out)


def route_keys(step: Step, s: Session) -> list[str]:
    if step.routes_by_age:
        return step.routes_by_age.get(s.answers.get("age", "unsure"), step.routes_by_age["unsure"])
    return step.routes


def route_lines(keys: list[str], actions: Actions) -> str:
    return "\n".join(f"- {actions.routes[k].title}" + (f" ({actions.routes[k].contact})" if actions.routes[k].contact else "") + f"\n  {actions.routes[k].detail}" for k in keys)


def advance(flow: Flow, s: Session, text: str, actions: Actions, today: date, now: float | None = None) -> str:
    """One reply from the person to the next message. A text that is not a valid choice repeats the question; it never ends the flow."""
    lang = s.lang
    if s.done:
        return flow.messages["close"].model_dump()[lang]
    step = flow.steps[s.step]
    if step.fields:
        value = " ".join(text.split())
        s.answers[step.fields[s.field_index].key] = value[: flow.settings.max_field_chars]
        s.field_index += 1
        if s.field_index < len(step.fields):
            return _prompt(flow, s)
        s.step, s.field_index = step.next, 0
    else:
        pick = text.strip()
        if not (pick.isdigit() and 1 <= int(pick) <= len(step.options)):
            return flow.messages["retry"].model_dump()[lang] + "\n\n" + _prompt(flow, s)
        option = step.options[int(pick) - 1]
        s.answers.update(option.set)
        s.step = option.next
    nxt = flow.steps[s.step]
    if not nxt.final:
        return _prompt(flow, s)
    s.done = True
    parts = []
    if nxt.say and nxt.say != "close":
        parts.append(flow.messages[nxt.say].model_dump()[lang])
    if nxt.summary:
        parts += [flow.messages["summary_title"].model_dump()[lang], summary(flow, s, today), flow.messages["summary_note"].model_dump()[lang]]
    keys = route_keys(nxt, s)
    parts += [flow.messages["routes_title"].model_dump()[lang] + " " + flow.messages["routes_language"].model_dump()[lang], route_lines(keys, actions)]
    if nxt.say == "close":
        parts.append(flow.messages["close"].model_dump()[lang])
    return "\n\n".join(parts)
