"""Lookalike-link check: is a link in a forward the real government or bank site, or something dressed up as one?

Plain code, no model and no network: the names come from config/official_domains.yaml. We never open the link (a scam page can
profile the visitor, and a shortener hides its target), so this says only what the name itself shows:
  official      the link's domain is in our list
  lookalike     the name imitates a listed one (look-alike characters, a typo, the real name inside a longer one, or the real
                domain used as a sub-name of someone else's), with the real domain shown
  shortened     a link shortener: where it leads cannot be seen without following it
  similar_name  the same name as a listed one under a different ending (pmkisan.com): may be unrelated
  gov_style     ends in gov.in or nic.in but is not in our list: not judged
  unknown       not in our list: this is NOT a claim that it is safe, only that it is not a site we know
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..config import config_dir

FILE = "official_domains.yaml"
CANDIDATE = re.compile(r"(?:https?://)?(?:[^\s/@:]+@)?(?:[\w-]+\.)+[\w-]{2,}(?::\d+)?(?:/[^\s]*)?", re.UNICODE | re.IGNORECASE)
TRAILING = ".,;:!?)]}>\"'"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Official(_Strict):
    domain: str
    owner: str
    category: str


class Settings(_Strict):
    max_edit_distance: int = Field(ge=0)
    min_label_chars: int = Field(gt=0)


class OfficialDomains(_Strict):
    checked_on: str
    settings: Settings
    suffixes: list[str]
    government_suffixes: list[str]
    tlds: list[str]
    homoglyphs: dict[str, str]
    shorteners: list[str]
    domains: list[Official]


def load_domains(path: Path | None = None) -> OfficialDomains:
    return OfficialDomains.model_validate(yaml.safe_load((path or config_dir() / FILE).read_text(encoding="utf-8")))


@dataclass
class LinkFinding:
    link: str
    host: str
    status: str                       # official, lookalike, similar_name, shortened, gov_style, unknown
    reason: str
    real_domain: str = ""             # the listed domain it imitates (lookalike) or is (official)
    owner: str = ""


def registered(host: str, suffixes: list[str]) -> tuple[str, str, str]:
    """(name, suffix, registered domain). 'portal.pmkisan.gov.in' -> ('pmkisan', 'gov.in', 'pmkisan.gov.in')."""
    labels = host.split(".")
    keep = 3 if ".".join(labels[-2:]) in suffixes else 2          # a two-label suffix such as gov.in takes one more label
    tail = labels[-keep:]
    return tail[0], ".".join(tail[1:]), ".".join(tail)


def skeleton(label: str, glyphs: dict[str, str]) -> str:
    text = unicodedata.normalize("NFKD", label.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    for k in sorted(glyphs, key=len, reverse=True):
        text = text.replace(k, glyphs[k])
    return text.replace("-", "")


def distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def candidates(text: str, d: OfficialDomains) -> list[tuple[str, str]]:
    """(link as written, host) for every link-like piece of text. Bare names count only with www. or a known ending."""
    out: list[tuple[str, str]] = []
    for m in CANDIDATE.finditer(text):
        raw = m.group(0).rstrip(TRAILING)
        has_scheme = raw.casefold().startswith(("http://", "https://"))  # forge: allow-literal URL scheme test, not a source
        parts = urlsplit(raw if has_scheme else "//" + raw)
        host = (parts.hostname or "").casefold().strip(".")
        if not host or "." not in host:
            continue
        if not (has_scheme or host.startswith("www.") or host.rsplit(".", 1)[-1] in d.tlds or host.startswith("xn--")):
            continue
        out.append((raw, host))
    return out


def check_host(raw: str, host: str, d: OfficialDomains) -> LinkFinding:
    s = d.settings
    ascii_host = host.encode("idna").decode("ascii") if not host.isascii() else host
    name, suffix, reg = registered(ascii_host, d.suffixes)
    shown, _s, _r = registered(host, d.suffixes)                # the name as typed, for look-alike character comparison
    userinfo = raw.split("@")[0] if "@" in raw.split("/", 3)[2 if "://" in raw else 0] else ""
    by_domain = {o.domain: o for o in d.domains}
    if reg in d.shorteners or ascii_host in d.shorteners:
        return LinkFinding(raw, host, "shortened", "a link shortener hides where it leads, and we do not open links: treat it as unchecked")
    if reg in by_domain:
        return LinkFinding(raw, host, "official", f"this is {by_domain[reg].owner}'s domain", reg, by_domain[reg].owner)

    labels = ascii_host.split(".")
    idn = not host.isascii() or any(lab.startswith("xn--") for lab in labels)
    for o in d.domains:
        o_name, _o_suffix, o_reg = registered(o.domain, d.suffixes)
        found, status = "", "lookalike"
        if userinfo and o_reg in userinfo.casefold():
            found = f"shows {o_reg} before an @ sign, but the link goes to {reg}"
        elif o_reg in ascii_host:
            found = f"contains the real domain {o_reg} but belongs to {reg}"
        elif name == o_name and len(name) >= s.min_label_chars:
            found, status = f"uses the name '{name}' under a different ending ({suffix}); it may be unrelated", "similar_name"
        elif skeleton(shown, d.homoglyphs) == skeleton(o_name, d.homoglyphs) and name != o_name:
            found = "differs from the real name only in look-alike characters or hyphens"
        elif len(o_name) >= s.min_label_chars and len(name) >= s.min_label_chars and distance(skeleton(shown, d.homoglyphs), skeleton(o_name, d.homoglyphs)) <= s.max_edit_distance:
            found = "is a near-miss spelling of the real name"
        elif o_name in name.split("-") or (len(o_name) >= s.min_label_chars and o_name in skeleton(shown, d.homoglyphs)):
            found = f"puts the real name '{o_name}' inside a longer, different one"
        if found:
            return LinkFinding(raw, host, status, f"{host} {found}; the listed domain is {o.domain} ({o.owner})", o.domain, o.owner)
    if idn:
        return LinkFinding(raw, host, "unknown", "uses non-Latin or encoded characters that can imitate other names; it matches no domain in our list")
    if suffix in d.government_suffixes:
        return LinkFinding(raw, host, "gov_style", f"ends in {suffix} but {reg} is not in our list, so it is not judged")
    return LinkFinding(raw, host, "unknown", f"{reg} is not in our list of official domains: this is not a statement that it is safe")


def check_links(text: str, d: OfficialDomains | None = None) -> list[LinkFinding]:
    d = d or load_domains()
    seen, out = set(), []
    for raw, host in candidates(text, d):
        if host not in seen:
            seen.add(host)
            out.append(check_host(raw, host, d))
    return out
