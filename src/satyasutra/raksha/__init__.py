"""Raksha: a guided, text-only support flow for people targeted with private images. Never accepts, stores or checks an image; files nothing.

`flow` is the short guided questions; `corpus`, `ask`, `ladder` and `drafts` are the grounded guide: official documents on disk, answers that quote them,
an escalation ladder with quoted sources, and letter drafts from typed fields.
"""

from .flow import Flow, Session, load_flow

__all__ = ["Flow", "Session", "load_flow"]
