"""Grounded answers (plan RR3): the model only picks and phrases, code decides what survives.

A sentence is kept only if it cites one of the retrieved passages and its quote appears verbatim in that passage. Every number in the sentence
(days, fees, phone numbers, section numbers) must also appear in the quote, and a sentence that promises an outcome is dropped. If nothing
survives, the reply is the refusal text: an unanswerable question is refused, never guessed. A danger message goes to the 112 text first,
before anything is retrieved or sent to a model.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..claims.extract import load_prompt
from ..errors import SourceError
from ..llm import GeminiClient
from .corpus import Chunk, CorpusIndex, Rag, WORD

ASK_PROMPT = "raksha_ask.txt"
TRANSLATE_PROMPT = "raksha_translate.txt"
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
DIGITS = re.compile(r"\d+")
MARKERS = ("<<<QUESTION", "QUESTION>>>", "<<<PASSAGES", "PASSAGES>>>")
LANG_NAMES = {"en": "English", "hi": "Hindi"}

ASK_SCHEMA = {
    "type": "OBJECT",
    "properties": {"sentences": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "text": {"type": "STRING"}, "passage": {"type": "INTEGER"}, "quote": {"type": "STRING"}},
        "required": ["text", "passage", "quote"]}}},
    "required": ["sentences"],
}
TRANSLATE_SCHEMA = {"type": "OBJECT", "properties": {"english": {"type": "STRING"}}, "required": ["english"]}


@dataclass
class Answer:
    sentences: list[dict] = field(default_factory=list)   # text, quote, source (the cited passage)
    refused: bool = False
    danger: bool = False
    english_query: str = ""
    dropped: int = 0                                      # sentences the checks removed
    retrieved: int = 0


def _clean(text: str) -> str:
    for m in MARKERS:
        text = text.replace(m, "")
    return " ".join(text.split())


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _numbers(text: str, words: dict[str, int]) -> set[str]:
    """Figures in a text as plain ASCII digit strings: Devanagari numerals and number words ("thirty") compare equal to their digits."""
    norm = unicodedata.normalize("NFKC", text)
    found = {"".join(str(unicodedata.digit(c, 0)) for c in run) for run in DIGITS.findall(norm)}
    return found | {str(words[w]) for w in WORD.findall(norm.casefold()) if w in words}


def stem(word: str) -> str:
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def content_words(text: str, rag: Rag) -> list[str]:
    stop = {w.casefold() for w in rag.stopwords}
    return [stem(w) for w in WORD.findall(text.casefold()) if len(w) >= rag.retrieval.min_word_chars and w not in stop]


def is_danger(text: str, rag: Rag) -> bool:
    low = text.casefold()
    return any(t.casefold() in low for t in rag.safety.danger_terms)


def retrieve(index: CorpusIndex, rag: Rag, english_query: str) -> list[Chunk]:
    """Keyword candidates, then a coverage floor: a passage must hold `min_coverage` of the question's distinct content words, or it is not returned."""
    words = list(dict.fromkeys(content_words(english_query, rag)))
    r = rag.retrieval
    if len(words) < r.min_words:
        return []
    scored = []
    for rank, c in enumerate(index.candidates(words, r.candidates)):
        have = {stem(w) for w in WORD.findall(c.text.casefold())}
        covered = sum(1 for w in words if w in have or any(h.startswith(w) for h in have))
        share = covered / len(words)
        if share >= r.min_coverage:
            scored.append((-share, rank, c))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [c for _, _, c in scored[: r.top_k]]


def to_english(client: GeminiClient, question: str) -> str:
    """A Hindi question is translated for retrieval only; the original is what the person sees."""
    prompt = load_prompt(TRANSLATE_PROMPT).replace("{question}", question)
    english = client.generate_json(prompt, TRANSLATE_SCHEMA).data.get("english")
    if not isinstance(english, str) or not english.strip():
        raise SourceError("gemini_llm", "BadOutput")
    return english.strip()


def _keep(item: dict, passages: list[Chunk], rag: Rag) -> dict | None:
    n, text, quote = item.get("passage"), item.get("text"), item.get("quote")
    if not (isinstance(n, int) and 1 <= n <= len(passages) and isinstance(text, str) and isinstance(quote, str)):
        return None
    chunk = passages[n - 1]
    if len(_norm(quote)) < rag.retrieval.min_quote_chars or _norm(quote) not in _norm(chunk.text):
        return None                                           # a quote that is not in the passage is a made-up quote
    if not _numbers(text, rag.retrieval.number_words) <= _numbers(quote, rag.retrieval.number_words):
        return None                                           # a figure the quote does not contain came from the model's memory
    if any(t.casefold() in text.casefold() for t in rag.safety.promise_terms):
        return None
    return {"text": " ".join(text.split()), "quote": quote.strip(), "source": chunk}


def route(text: str, rag: Rag) -> str:
    """What a chat message is for, by plain word lists: danger first, then how the person feels, then the ladder, then a letter, otherwise a question for the documents."""
    low = text.casefold()
    if is_danger(text, rag):
        return "danger"
    if any(t.casefold() in low for t in rag.chat.support_terms):
        return "support"
    if any(t.casefold() in low for t in rag.chat.ladder_terms):
        return "ladder"
    if any(t.casefold() in low for t in rag.chat.draft_terms):
        return "draft"
    return "ask"


def answer_question(client: GeminiClient, index: CorpusIndex, rag: Rag, question: str, lang: str, context: str = "") -> Answer:
    question = _clean(question)
    out = Answer()
    if is_danger(question, rag):
        out.danger = True
        return out
    english = to_english(client, question) if DEVANAGARI.search(question) else question
    out.english_query = english
    # A short follow-up ("and then?") borrows the previous English question for the search only; the model still sees just the current question.
    earlier = _clean(context)
    short = len(set(content_words(english, rag))) < rag.chat.short_question_words
    passages = retrieve(index, rag, f"{english} {earlier}" if earlier and short and not DEVANAGARI.search(earlier) else english)
    out.retrieved = len(passages)
    if not passages:
        out.refused = True
        return out
    listing = "\n".join(f"{i}. {c.title} ({c.publisher}) | {_clean(c.text)}" for i, c in enumerate(passages, start=1))
    prompt = (load_prompt(ASK_PROMPT).replace("{lang}", LANG_NAMES.get(lang, "English")).replace("{question}", english if lang == "en" else question)
              .replace("{passages}", listing))
    items = client.generate_json(prompt, ASK_SCHEMA).data.get("sentences")
    if not isinstance(items, list):
        raise SourceError("gemini_llm", "BadOutput")
    for item in items:
        kept = _keep(item, passages, rag) if isinstance(item, dict) else None
        if kept:
            out.sentences.append(kept)
        else:
            out.dropped += 1
    out.refused = not out.sentences
    return out


def public(a: Answer, rag: Rag, lang: str, danger_text: str) -> dict:
    """What the API returns: each sentence with its quote and a source that names the document, page, link, and the date we fetched it."""
    if a.danger:
        return {"danger": True, "message": danger_text, "sentences": []}
    sources = lambda c: {"id": c.pid, "title": c.title, "publisher": c.publisher, "url": c.url, "page": c.page, "retrieved_on": c.retrieved_on, "hand_downloaded": c.hand_downloaded}
    return {"danger": False, "refused": a.refused, "refusal": getattr(rag.refusal, lang) if a.refused else "",
            "sentences": [{"text": s["text"], "quote": s["quote"], "source": sources(s["source"])} for s in a.sentences],
            "dropped": a.dropped, "retrieved": a.retrieved, "note": getattr(rag.unreviewed, lang)}
