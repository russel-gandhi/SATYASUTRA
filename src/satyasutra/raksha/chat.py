"""The situation conversation: ask the few things that decide the help, then write a short kind reply from vetted facts.

Plain code does the work that must be right: it spots that a message is about private images, reads what it can from the words (is it a threat, already shared,
or faked; is the person under 18), asks for what is missing with chips, and picks the first steps from the ladder by age and situation. A model only phrases
the reply, from those facts. The reply is kept only if every figure, link or law-like word in it is in the facts and it promises nothing; otherwise a fixed
warm reply is used. The conversation state (the profile) lives in the page and comes back with each message: the server keeps nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..act.plan import Actions, is_intimate
from ..claims.extract import load_prompt
from ..errors import SourceError
from ..llm import GeminiClient
from .ask import _numbers, is_danger
from .corpus import Rag
from .ladder import Ladder

PROMPT = "raksha_plan.txt"
SCHEMA = {"type": "OBJECT", "properties": {"message": {"type": "STRING"}}, "required": ["message"]}
LANG_NAMES = {"en": "English", "hi": "Hindi"}
LINKTOKEN = re.compile(r"(?:https?://|www\.)\S+|[\w-]+(?:\.[\w-]+)*\.(?:gov|org|com|in)\S*", re.IGNORECASE)
AGES = ("adult", "minor", "unsure")
WHATS = ("threatened", "shared", "faked")


@dataclass
class Turn:
    kind: str                                   # "ask" or "plan"
    profile: dict
    say: str = ""
    options: list[dict] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)      # ladder rung ids, in order
    composed: bool = False                      # the model's reply passed the checks (false = the fixed reply was used)


def _has(text: str, words: list[str]) -> bool:
    low = text.casefold()
    return any(w.casefold() in low for w in words)


def is_incident(text: str, rag: Rag, actions: Actions) -> bool:
    return is_intimate(text, actions) or _has(text, rag.chat.incident_terms)


def merge(profile: dict, text: str, rag: Rag) -> dict:
    """What the words themselves say about age and situation. Only explicit words count: an age is never guessed."""
    p = {k: v for k, v in profile.items() if k in ("age", "what", "situation") and (k != "age" or v in AGES) and (k != "what" or v in WHATS)}
    p["situation"] = True
    c = rag.chat
    if "age" not in p:
        if _has(text, c.age_terms["minor"]):
            p["age"] = "minor"
        elif _has(text, c.age_terms["adult"]):
            p["age"] = "adult"
    if "what" not in p:
        for what in ("faked", "shared", "threatened"):          # a faked picture that is also shared is told as faked; "leaked" (done) beats "leak" (threat)
            if _has(text, c.what_terms[what]):
                p["what"] = what
                break
    return p


def next_turn(profile: dict, text: str, rag: Rag, lang: str) -> Turn:
    first = not profile.get("situation")
    p = merge(profile, text, rag)
    c = rag.chat
    if "age" not in p:
        q = c.ask_age
        opener = f"{getattr(c.opener, lang)} " if first else ""
        return Turn("ask", p, opener + getattr(q.say, lang), [{"label": getattr(o.label, lang), "set": o.set} for o in q.options])
    if "what" not in p:
        q = c.ask_what
        opener = f"{getattr(c.opener, lang)} " if first else ""
        return Turn("ask", p, opener + getattr(q.say, lang), [{"label": getattr(o.label, lang), "set": o.set} for o in q.options])
    hashed = "stopncii" if p["age"] == "adult" else "take_it_down"      # an unsure age is treated as under 18, as everywhere in Raksha
    steps = [hashed if s == "hash" else s for s in c.plan[p["what"]]]
    return Turn("plan", p, steps=steps)


def _situation(profile: dict, lang: str) -> str:
    what = {"threatened": "someone is threatening to share their private pictures", "shared": "their private pictures have already been shared",
            "faked": "pictures of them were changed or faked"}[profile["what"]]
    age = "18 or older" if profile["age"] == "adult" else "under 18 or not sure"
    return f"{what}; the person is {age}. Reply language: {LANG_NAMES.get(lang, 'English')}."


def facts_text(rungs: list[dict], aid: list[str]) -> str:
    return "\n".join([*(f"{r['title']}. {r['how']} {r['wait']}" for r in rungs), *aid])


def vet(message: str, facts: str, rag: Rag) -> bool:
    """A composed reply is kept only if it stays inside the facts: no figure, link or law-like word that is not in them, no promise, not too long."""
    if not message.strip() or len(message) > rag.chat.max_message_chars:
        return False
    if not _numbers(message, rag.retrieval.number_words) <= _numbers(facts, rag.retrieval.number_words):
        return False
    if not {m.casefold().rstrip(".,") for m in LINKTOKEN.findall(message)} <= {m.casefold().rstrip(".,") for m in LINKTOKEN.findall(facts)}:
        return False
    low, fl = message.casefold(), facts.casefold()
    if any(t.casefold() in low and t.casefold() not in fl for t in rag.chat.forbidden_terms):
        return False
    return not any(t.casefold() in low for t in rag.safety.promise_terms)


def compose(client: GeminiClient, rag: Rag, profile: dict, rungs: list[dict], lang: str) -> str | None:
    """The model's kind phrasing of the steps, or None (model unavailable or reply rejected): the caller then uses the fixed reply."""
    aid = [getattr(a, lang) for a in rag.chat.first_aid]
    prompt = (load_prompt(PROMPT).replace("{lang}", LANG_NAMES.get(lang, "English")).replace("{situation}", _situation(profile, lang))
              .replace("{steps}", "\n".join(f"{i}. {r['title']}: {r['how']}" for i, r in enumerate(rungs, start=1)))
              .replace("{aid}", "\n".join(f"- {a}" for a in aid)))
    try:
        message = client.generate_json(prompt, SCHEMA).data.get("message")
    except SourceError:
        return None
    if not isinstance(message, str):
        return None
    message = " ".join(message.split())
    return message if vet(message, facts_text(rungs, aid), rag) else None
