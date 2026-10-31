"""Notice checker: a photographed or screenshotted notice, circular or message is read by Gemini vision, then checked by plain code.

Gemini only copies what is printed (text, who it says it is from, links, phone numbers, what it asks for). The findings come from code:
  - every link name against the official-domain list (names only; links are never opened),
  - whether the notice claims an official issuer we hold text for (RBI, SEBI) and, if so, whether a matching release is in that text,
  - scam wording and requests (OTP, fees, links) from the same word lists as the next-steps card.
Nothing here says a notice is genuine or fake. "No matching release" only means none is among the few latest items we hold; the findings say so.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image, UnidentifiedImageError

from .act import build_plan, load_actions
from .claims.extract import load_prompt
from .config import Config
from .errors import SourceError
from .llm import GeminiClient
from .scam import check_links
from .sources.index import OfficialIndex
from .sources.news import content_words, coverage, weights

PROMPT = "notice.txt"
MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}

SCHEMA = {
    "type": "OBJECT",
    "properties": {"text": {"type": "STRING"}, "issuer": {"type": "STRING"}, "urls": {"type": "ARRAY", "items": {"type": "STRING"}},
                   "phone_numbers": {"type": "ARRAY", "items": {"type": "STRING"}}, "asks_for": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["text", "issuer", "urls", "phone_numbers", "asks_for"],
}


class NoticeError(ValueError):
    """The image could not be read as a notice; the message is safe to show."""


@dataclass
class NoticeResult:
    text: str
    issuer: str
    urls: list[str]
    phone_numbers: list[str]
    asks_for: list[str]
    findings: list[dict] = field(default_factory=list)       # {"kind": ..., "level": "red_flag" | "note", "text": ...}
    official_match: dict | None = None
    plan: dict = field(default_factory=dict)


def _mime(data: bytes, limit: int) -> str:
    if not data or len(data) > limit:
        raise NoticeError(f"send 1 to {limit} bytes of image")
    try:
        fmt = Image.open(io.BytesIO(data)).format or ""
    except (UnidentifiedImageError, OSError):
        raise NoticeError("not a readable image") from None
    if fmt not in MIME:
        raise NoticeError("send a JPEG, PNG or WebP image")
    return MIME[fmt]


def read_notice(client: GeminiClient, data: bytes, cfg: Config) -> dict:
    mime = _mime(data, cfg.runtime.notice.max_image_bytes)
    try:
        reply = client.generate_json(load_prompt(PROMPT), SCHEMA, images=[(mime, data)])
    except SourceError as exc:
        raise NoticeError(f"the language model could not read the image ({type(exc).__name__}); nothing was checked") from None
    d = reply.data
    return {"text": str(d.get("text", "")), "issuer": str(d.get("issuer", "")), "urls": [str(u) for u in d.get("urls", []) if isinstance(u, str)],
            "phone_numbers": [str(n) for n in d.get("phone_numbers", []) if isinstance(n, str)], "asks_for": [str(a) for a in d.get("asks_for", []) if isinstance(a, str)]}


def claims_official_issuer(issuer: str, text: str, cfg: Config) -> bool:
    haystack = f"{issuer} {text[:200]}".casefold()
    return any(a.casefold() in haystack for a in cfg.runtime.notice.issuer_aliases)


def check_notice(client: GeminiClient, index: OfficialIndex, data: bytes, cfg: Config) -> NoticeResult:
    s = cfg.runtime.notice
    read = read_notice(client, data, cfg)
    text = read["text"].strip()
    r = NoticeResult(text, read["issuer"], read["urls"], read["phone_numbers"], read["asks_for"])
    if not text:
        r.findings.append({"kind": "unreadable", "level": "note", "text": "No readable text was found in the image, so nothing could be checked."})
        return r
    scan = " ".join([text, *read["urls"]])
    links = check_links(scan)
    for f in links:
        if f.status in ("lookalike", "similar_name"):
            r.findings.append({"kind": "link", "level": "red_flag", "text": f"Suspicious link {f.link}: {f.reason}."})
        elif f.status == "shortened":
            r.findings.append({"kind": "link", "level": "red_flag", "text": f"{f.link} is a shortened link, so you cannot see where it leads."})
        elif f.status == "official":
            r.findings.append({"kind": "link", "level": "note", "text": f"{f.link} is on a domain in our official list ({f.owner or f.host}). That checks the name only, not the page."})
        else:
            r.findings.append({"kind": "link", "level": "note", "text": f"{f.link}: {f.reason}"})
    plan = build_plan(scan, links, [], load_actions())
    r.plan = {"intent": plan.intent, "why": plan.why, "routes": plan.routes, "notice": plan.notice}
    if plan.intent == "scam_forward":
        r.findings.append({"kind": "wording", "level": "red_flag", "text": f"It looks like a scam attempt: {plan.why}."})
    if read["asks_for"]:
        r.findings.append({"kind": "asks", "level": "note", "text": "It asks the reader to: " + "; ".join(read["asks_for"][:4]) + "."})
    if claims_official_issuer(read["issuer"], text, cfg):
        match = _official_match(index, text, cfg)
        if match:
            r.official_match = match
            r.findings.append({"kind": "official", "level": "note", "text": f"It says it is from an official issuer, and a matching item is in {match['publisher']}'s own feed: {match['title']}. Compare the details yourself at {match['url']}."})
        else:
            r.findings.append({"kind": "official", "level": "red_flag" if plan.intent == "scam_forward" else "note",
                               "text": "It says it is from an official issuer, but no matching release is in that issuer's feed as we hold it. We hold only the latest items, so this does not prove it is fake: check the issuer's own website."})
    return r


def _official_match(index: OfficialIndex, text: str, cfg: Config) -> dict | None:
    s = cfg.runtime.notice
    words = content_words(text, cfg)
    if not words:
        return None
    w = weights(words, index.texts(), cfg)
    for p in index.search(" ".join(words), s.max_passages * 4, cfg.runtime.official.min_token_chars):
        share, matched = coverage(words, p.text, w, cfg)
        if share >= s.min_coverage and matched >= s.min_matched_words:
            return {"publisher": p.publisher, "title": p.title, "url": p.url, "coverage": round(share, 2)}
    return None
