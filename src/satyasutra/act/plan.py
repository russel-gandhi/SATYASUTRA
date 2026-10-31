"""Verify -> Act. Plain code on top of the verdict and the link check; nothing is filed, sent or called for anyone (Bible D42).

The intent comes from plain matching against word lists in config/actions.yaml: money already lost, a scam attempt (a lookalike or
shortened link, or several scam words), or a false claim (a REFUTED verdict). Each route carries its source page, and the config says
plainly that the routes were found in secondary sources and not yet confirmed on the official sites.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..config import config_dir
from ..ledger import LedgerStore
from ..scam import LinkFinding

FILE = "actions.yaml"
SUSPECT_LINKS = {"lookalike", "similar_name", "shortened"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Route(_Strict):
    title: str
    detail: str
    contact: str = ""
    url: str = ""
    source_url: str = ""


class Settings(_Strict):
    min_scam_words: int = Field(gt=0)
    max_text_chars: int = Field(gt=0)


class Actions(_Strict):
    checked_on: str
    confirmed_on_official_site: bool
    settings: Settings
    scam_words: list[str]
    loss_phrases: list[str]
    routes: dict[str, Route]
    intents: dict[str, list[str]]


def load_actions(path: Path | None = None) -> Actions:
    return Actions.model_validate(yaml.safe_load((path or config_dir() / FILE).read_text(encoding="utf-8")))


@dataclass
class ActionPlan:
    intent: str                         # money_lost, scam_forward, false_claim or none
    why: str
    routes: list[dict] = field(default_factory=list)
    notice: str = ""


def _has(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(phrase.casefold()) + r"(?!\w)", text.casefold()) is not None


def build_plan(text: str, links: list[LinkFinding], labels: list[str], a: Actions) -> ActionPlan:
    scam_hits = [w for w in a.scam_words if _has(text, w)]
    suspect = [link for link in links if link.status in SUSPECT_LINKS]
    if any(_has(text, p) for p in a.loss_phrases):
        intent, why = "money_lost", "the message says money has already been paid or lost"
    elif suspect or len(scam_hits) >= a.settings.min_scam_words:
        why = (f"it contains a {suspect[0].status.replace('_', ' ')} link ({suspect[0].host})" if suspect else f"it uses scam wording ({', '.join(scam_hits[:3])})")
        intent = "scam_forward"
    elif "REFUTED" in labels:
        intent, why = "false_claim", "the claim was checked and refuted"
    else:
        return ActionPlan("none", "nothing here calls for a next step beyond the verdict")
    routes = [a.routes[r].model_dump() | {"id": r} for r in a.intents[intent]]
    where = " against the official sites." if a.confirmed_on_official_site else " in secondary sources, not on the official sites: confirm them there."
    notice = f"These steps come from public guidance checked on {a.checked_on}{where} We do not file, send or call anything for you."
    return ActionPlan(intent, why, routes, notice)


def build_pack(text: str, plan: ActionPlan, links: list[LinkFinding], ledger_seqs: list[int], ledger: LedgerStore, a: Actions, now: datetime | None = None) -> str:
    """A plain-text evidence pack: what was received, what we found, and ledger entries that show when and how it was checked."""
    now = now or datetime.now(timezone.utc)
    lines = ["EVIDENCE PACK prepared by SATYASUTRA", f"Prepared at: {now.isoformat()}",
             "This is a record for you to attach to a complaint you file yourself. We have not filed or sent anything.", "",
             "WHAT WAS RECEIVED", text[:a.settings.max_text_chars], ""]
    if links:
        lines.append("LINKS IN THE MESSAGE (names checked only; the links were never opened)")
        lines += [f"- {link.link} -> {link.status}: {link.reason}" for link in links]
        lines.append("")
    lines.append(f"WHAT WE FOUND (intent: {plan.intent}; {plan.why})")
    entries = [e for e in (ledger.get(s) for s in ledger_seqs) if e]
    for e in entries:
        p = e.payload
        lines += [f"- Claim checked: verdict {p.get('label')} ({p.get('confidence_band')} confidence), ledger entry #{e.seq}, recorded {e.created_at.isoformat()}",
                  f"  entry hash {e.hash}"]
        lines += [f"  reason: {r}" for r in p.get("reasons", [])[:4]]
        lines += [f"  source: {s.get('publisher') or 'source'} {s.get('url')}" for s in p.get("evidence", [])]
    if not entries:
        lines.append("- No claim verdict was recorded for this message.")
    lines += ["", "HOW TO CHECK THIS RECORD",
              "Each ledger entry hash chains to the one before it; scripts/verify_ledger.py in the project prints whether the chain is intact.", "",
              "WHERE TO REPORT (you file; we do not)"]
    for r in plan.routes:
        lines.append(f"- {r['title']}" + (f" ({r['contact']})" if r.get("contact") else "") + (f" {r['url']}" if r.get("url") else ""))
    lines += ["", plan.notice]
    return "\n".join(lines)
