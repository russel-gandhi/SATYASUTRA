"""The escalation ladder (plan RR4): who to approach, in what order, and what to bring, each rung tied to a quoted official passage.

A rung exists only with at least one source; a quote is checked against the index every time the ladder is served, so a document that changed
or a mistyped quote shows as "not confirmed" instead of passing as fact. A wait time is shown only when a document states it. The ladder is a
directed graph with no cycle, checked on load. Nothing here is filed or sent; the person keeps their own place in the ladder (the page holds it, we store nothing).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from ..config import config_dir
from ..errors import ConfigError
from .corpus import CorpusIndex, Text

FILE = "escalation.yaml"
AGES = ("adult", "minor", "unsure")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(_Strict):
    passage: str
    quote: str


class Rung(_Strict):
    title: Text
    how: Text
    attach: Text | None = None
    wait: Literal["documented", "not_stated"]
    wait_text: Text | None = None
    next: list[str]
    sources: list[Source]

    @model_validator(mode="after")
    def _check(self) -> "Rung":
        if not self.sources:
            raise ValueError("a rung needs at least one source passage")
        if (self.wait == "documented") != (self.wait_text is not None):
            raise ValueError("wait_text is given exactly when wait is documented")
        return self


class Ladder(_Strict):
    reviewed_by: str
    reviewed_on: str
    state: str
    not_covered: Text
    no_time_limit: Text
    starts: dict[str, list[str]]
    rungs: dict[str, Rung]

    @model_validator(mode="after")
    def _graph(self) -> "Ladder":
        names = set(self.rungs)
        for age, starts in self.starts.items():
            if age not in AGES or not set(starts) <= names:
                raise ValueError(f"starts[{age}] must name known rungs")
        if set(self.starts) != set(AGES):
            raise ValueError("starts must cover adult, minor and unsure")
        for rid, r in self.rungs.items():
            if not set(r.next) <= names:
                raise ValueError(f"rung {rid} leads to an unknown rung")
        state: dict[str, str] = {}

        def visit(n: str) -> None:
            if state.get(n) == "open":
                raise ValueError(f"the ladder has a cycle through {n}")
            if state.get(n) == "done":
                return
            state[n] = "open"
            for m in self.rungs[n].next:
                visit(m)
            state[n] = "done"

        for n in names:
            visit(n)
        reached = {s for ss in self.starts.values() for s in ss}
        frontier = list(reached)
        while frontier:
            for m in self.rungs[frontier.pop()].next:
                if m not in reached:
                    reached.add(m)
                    frontier.append(m)
        if reached != names:
            raise ValueError(f"rungs not reachable from any start: {sorted(names - reached)}")
        return self


def load_ladder(path: Path | None = None) -> Ladder:
    try:
        return Ladder.model_validate(yaml.safe_load((path or config_dir() / FILE).read_text(encoding="utf-8")))
    except (ValueError, OSError) as exc:
        raise ConfigError(f"{FILE}: {exc}") from None


def _confirm(index: CorpusIndex, src: Source) -> dict:
    """Is the quote in the cited passage right now? The index is the only judge."""
    chunk = index.get(src.passage)
    norm = lambda t: " ".join(t.split()).casefold()
    ok = chunk is not None and norm(src.quote) in norm(chunk.text)
    base = {"quote": src.quote, "passage": src.passage, "confirmed": ok}
    if chunk is not None:
        base.update({"title": chunk.title, "publisher": chunk.publisher, "url": chunk.url, "page": chunk.page, "retrieved_on": chunk.retrieved_on})
    return base


def view(ladder: Ladder, index: CorpusIndex, age: str, lang: str) -> dict:
    """The ladder for one age group as plain data: rungs in walking order, each with its wait, what to bring, and confirmed sources."""
    age = age if age in AGES else "unsure"
    order: list[str] = []
    for start in ladder.starts[age]:
        stack = [start]
        while stack:
            n = stack.pop(0)
            if n not in order:
                order.append(n)
                stack += ladder.rungs[n].next
    out = []
    for rid in order:
        r = ladder.rungs[rid]
        wait = getattr(r.wait_text, lang) if r.wait_text else getattr(ladder.no_time_limit, lang)
        out.append({"id": rid, "title": getattr(r.title, lang), "how": getattr(r.how, lang), "attach": getattr(r.attach, lang) if r.attach else "",
                    "wait": wait, "wait_documented": r.wait == "documented", "next": r.next, "start": rid in ladder.starts[age],
                    "sources": [_confirm(index, s) for s in r.sources]})
    return {"state": ladder.state, "age": age, "rungs": out, "not_covered": getattr(ladder.not_covered, lang),
            "reviewed": bool(ladder.reviewed_by), "index_ready": index.ready()}
