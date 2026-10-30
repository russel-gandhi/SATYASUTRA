"""Typed failures. A dependency never fails by returning an empty list; it raises one of these (Bible I8)."""

from __future__ import annotations


class ForgeError(Exception):
    """Base class for every error the system raises on purpose."""


class ConfigError(ForgeError):
    """A config file is missing, malformed, or a required secret is not in the environment."""


class SourceError(ForgeError):
    """A live source could not give a usable answer."""

    def __init__(self, source_id: str, detail: str = ""):
        self.source_id = source_id
        self.detail = detail
        super().__init__(f"{type(self).__name__}[{source_id}]: {detail}" if detail else f"{type(self).__name__}[{source_id}]")


class Unreachable(SourceError):
    """Network failure, timeout or a server error."""


class RateLimited(SourceError):
    """The source asked us to slow down."""


class SchemaChanged(SourceError):
    """The source answered, but not in the shape the adapter expects."""


class QuotaExhausted(SourceError):
    """A budget (ours, in policy.yaml, or the provider's) is used up."""


class AuthRefused(SourceError):
    """The source rejected our credentials (HTTP 401 or 403)."""


class ContentBlocked(SourceError):
    """The source answered but declined to process this input (a safety block), so there is no output to use."""

