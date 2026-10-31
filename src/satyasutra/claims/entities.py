"""Name the entities in a claim (Gemini), then ground each to Wikidata. Context for a reader; it never changes a verdict.

Every name Gemini returns must appear in the claim text (case-insensitive), so the model cannot add entities the claim does not
mention. A failed call or lookup yields fewer entities and a recorded reason, never an error to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..config import Config
from ..errors import SourceError
from ..llm import GeminiClient
from ..sources.wikidata import Entity
from .extract import load_prompt

PROMPT = "entities.txt"
CLAIM_OPEN, CLAIM_CLOSE = "<<<CLAIM", "CLAIM>>>"
SCHEMA = {"type": "OBJECT", "properties": {"entities": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": ["entities"]}


@dataclass
class Grounded:
    entities: list[Entity] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)     # names found in the claim that Wikidata had no entry for
    error: str = ""                                        # exception class when a step failed


def ground_entities(client: GeminiClient, config: Config, claim_text: str, lookup: Callable[[str], Entity | None]) -> Grounded:
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
        name = " ".join(raw.split()) if isinstance(raw, str) else ""
        if not name or name.casefold() in seen or name.casefold() not in safe.casefold():
            continue                                        # empty, repeated, or not in the claim: the model may not add entities
        if len(seen) >= settings.max_entities:
            break
        seen.add(name.casefold())
        try:
            found = lookup(name)
        except SourceError as exc:
            out.error = type(exc).__name__
            continue
        if found:
            out.entities.append(found)
        else:
            out.unmatched.append(name)
    return out
