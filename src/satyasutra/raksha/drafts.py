"""Letter drafts from typed fields only (plan RR5): no model call, so nothing outside the fields and the fixed wording can appear.

Each template placeholder must be a known field, `subject` or `note`, checked on load. A typed value is flattened to one line and cut to
`max_field_chars`; a missing one shows a visible "not given" marker instead of an invented value. The wording never describes the images.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from ..config import config_dir
from ..errors import ConfigError
from .corpus import Text

FILE = "raksha_drafts.yaml"
PLACEHOLDER = re.compile(r"\{(\w+)\}")
WHATS = ("threatened", "shared", "faked")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Draft(_Strict):
    title: Text
    body: Text


class Drafts(_Strict):
    reviewed_by: str
    reviewed_on: str
    max_field_chars: int
    missing: Text
    fields: list[str]
    note: Text
    subjects: dict[str, dict[str, Text]]
    drafts: dict[str, Draft]

    @model_validator(mode="after")
    def _placeholders(self) -> "Drafts":
        allowed = set(self.fields) | {"subject"}
        for name, d in self.drafts.items():
            for lang in ("en", "hi"):
                unknown = set(PLACEHOLDER.findall(getattr(d.body, lang))) - allowed
                if unknown:
                    raise ValueError(f"draft {name} ({lang}) uses unknown placeholders {sorted(unknown)}")
        if set(self.subjects) != {"adult", "minor"} or any(set(v) != set(WHATS) for v in self.subjects.values()):
            raise ValueError("subjects must cover adult and minor, each with threatened, shared and faked")
        return self


def load_drafts(path: Path | None = None) -> Drafts:
    try:
        return Drafts.model_validate(yaml.safe_load((path or config_dir() / FILE).read_text(encoding="utf-8")))
    except (ValueError, OSError) as exc:
        raise ConfigError(f"{FILE}: {exc}") from None


def render(d: Drafts, kind: str, lang: str, fields: dict[str, str], age: str, what: str) -> str:
    if kind not in d.drafts:
        raise KeyError(kind)
    lang = lang if lang in ("en", "hi") else "en"
    unknown = set(fields) - set(d.fields)
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
    values = {k: " ".join(str(v).split())[: d.max_field_chars] for k, v in fields.items() if str(v).strip() and str(v).strip() != "-"}
    values = {k: values.get(k) or getattr(d.missing, lang) for k in d.fields}
    # An unsure age is treated as under 18, as everywhere else in Raksha.
    values["subject"] = getattr(d.subjects["adult" if age == "adult" else "minor"][what if what in WHATS else "shared"], lang)
    draft = d.drafts[kind]
    body = PLACEHOLDER.sub(lambda m: values[m.group(1)], getattr(draft.body, lang)).strip()
    return f"{getattr(draft.title, lang)}\n\n{body}\n\n{getattr(d.note, lang)}"
