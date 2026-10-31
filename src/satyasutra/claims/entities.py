"""Name the entities in a claim (Gemini), then ground each to Wikidata. Context for a reader; it never changes a verdict.

Every name Gemini returns must appear in the claim text (case-insensitive), so the model cannot add entities the claim does not
mention. A failed call or lookup yields fewer entities and a recorded reason, never an error to the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from ..config import Config
from ..errors import SourceError
from ..llm import GeminiClient
from ..sources.wikidata import Entity
from .extract import load_prompt

PROMPT = "entities.txt"
CLAIM_OPEN, CLAIM_CLOSE = "<<<CLAIM", "CLAIM>>>"
SCHEMA = {
    "type": "OBJECT",
    "properties": {"entities": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {"name": {"type": "STRING"}, "search": {"type": "STRING"}},
        "required": ["name", "search"],
    }}},
    "required": ["entities"],
}


@dataclass
class Grounded:
    entities: list[Entity] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)     # names found in the claim that Wikidata had no entry for
    error: str = ""                                        # exception class when a step failed


def mentions_death(text: str, terms: list[str]) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(t.casefold()) + r"(?!\w)", text.casefold()) for t in terms)


def ground_entities(client: GeminiClient, config: Config, claim_text: str, lookup: Callable[[str], Entity | None],
                    death: Callable[[str], tuple[str, int] | None] | None = None) -> Grounded:
    settings = config.runtime.knowledge_graph
    out = Grounded()
    safe = claim_text.replace(CLAIM_OPEN, "").replace(CLAIM_CLOSE, "")
    prompt = load_prompt(PROMPT).replace("{max_entities}", str(settings.max_entities)).replace("{claim}", safe)
    try:
        names = client.generate_json(prompt, SCHEMA).data.get("entities", [])
    except SourceError as exc:
        out.error = type(exc).__name__
        return out
    if not isinstance(names, list):
        out.error = "BadOutput"
        return out
    seen: set[str] = set()
    for raw in names:
        item = raw if isinstance(raw, dict) else {}
        name = " ".join(str(item.get("name", "")).split())
        search = " ".join(str(item.get("search", "")).split()) or name       # a fuller name for the lookup, to tell "UPI" from a town called Upi
        if not name or name.casefold() in seen or name.casefold() not in safe.casefold():
            continue                                        # empty, repeated, or not in the claim: the model may not add entities
        if len(seen) >= settings.max_entities:
            break
        seen.add(name.casefold())
        try:
            found = lookup(search)
        except SourceError as exc:
            out.error = type(exc).__name__
            continue
        if found:
            found.name = name                                        # shown under the words the claim used
            if death and mentions_death(safe, settings.death_terms):
                try:
                    record = death(found.id)
                except SourceError as exc:
                    out.error = type(exc).__name__
                    record = None
                if record:
                    found.died, found.died_references = record
            out.entities.append(found)
        else:
            out.unmatched.append(name)
    return out
