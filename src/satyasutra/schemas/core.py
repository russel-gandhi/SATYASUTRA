"""Shared contracts (BUILD_MAP section 5). Every lane codes to these types.

The validators are the point: a verdict that breaks a rule is not rejected, it is *downgraded to the
honest default* and flagged COERCED, so "no evidence" can never turn into a confident answer (Bible I1, I11).
Changing a field after the contract freeze needs every lane owner's agreement and a SCHEMA_VERSION bump.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1"

ClaimLabel = Literal["SUPPORTED", "REFUTED", "MISSING_CONTEXT", "UNVERIFIABLE_YET", "NOT_A_CLAIM"]
MediaLabel = Literal["AUTHENTIC_IN_CONTEXT", "OUT_OF_CONTEXT", "EDITED", "SYNTHETIC", "INCONCLUSIVE"]
CLAIM_LABELS = frozenset(get_args(ClaimLabel))
MEDIA_LABELS = frozenset(get_args(MediaLabel))

ALLOWED_FLAGS = frozenset(
    {
        "CONFLICT", "TRANSLATED", "GATE_FAILED", "SYNTHETIC_SUSPECTED", "EDITED_REGION",
        "STALE_SOURCE", "PROVENANCE_LIMITED", "DEGRADED", "COERCED", "HUMAN_REVIEW",
    }
)

# Labels that make a positive statement and therefore need evidence behind them.
CLAIM_DECISIVE = frozenset({"SUPPORTED", "REFUTED", "MISSING_CONTEXT"})
MEDIA_DECISIVE = frozenset({"AUTHENTIC_IN_CONTEXT", "OUT_OF_CONTEXT", "EDITED", "SYNTHETIC"})
TRUSTED_TIERS = frozenset({1, 2})


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MediaRef(_Model):
    id: str
    kind: Literal["image", "audio", "video"]
    sha256: str
    mime: str
    size_bytes: int = Field(ge=0)
    source_url: str | None = None


class Post(_Model):
    id: str
    source_id: str
    url: str
    author_hash: str                      # a salted hash, never a handle (Bible D27)
    text: str
    lang: str | None = None
    media: list[MediaRef] = Field(default_factory=list)
    posted_at: datetime
    fetched_at: datetime


class Claim(_Model):
    id: str
    post_id: str
    text_en: str
    text_orig: str
    span: tuple[int, int]
    lang: str
    translated_by: str | None = None
    time_ref: str | None = None           # used by the out-of-context check
    place_ref: str | None = None

    @model_validator(mode="after")
    def _span_ok(self) -> "Claim":
        if self.span[0] < 0 or self.span[1] < self.span[0]:
            raise ValueError("span must be (start, end) with 0 <= start <= end")
        return self


class Evidence(_Model):
    role: Literal["supports", "contradicts", "context", "earlier_appearance"]
    route: Literal["verifier", "factcheck", "contrast", "nli", "kg", "provenance", "detector", "web"]
    source_id: str
    url: str
    fetched_at: datetime
    tier: Literal[1, 2, 3, 4]
    passage: str
    passage_sha256: str
    support_prob: float | None = Field(default=None, ge=0.0, le=1.0)
    model: str = ""
    model_sha256: str = ""
    contrast_claim: str | None = None      # set when route == "contrast"
    publisher: str = ""                    # the fact-checker or outlet, when the route has one
    rating: str = ""                       # the publisher's own rating text, never rewritten
    reviewed_claim: str = ""               # the claim the publisher reviewed (may be only similar to ours)
    reviewed_at: datetime | None = None

    @model_validator(mode="after")
    def _integrity(self) -> "Evidence":
        if sha256_text(self.passage) != self.passage_sha256:
            raise ValueError("passage_sha256 does not match the passage text")
        if self.route == "contrast" and not self.contrast_claim:
            raise ValueError("a contrast evidence record must carry its contrast_claim")
        return self


class Verdict(_Model):
    id: str
    target_id: str
    axis: Literal["claim", "media"]
    label: ClaimLabel | MediaLabel
    confidence_band: Literal["low", "medium", "high"] = "low"   # never a fake-precise percentage
    evidence: list[Evidence] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)

    @field_validator("flags")
    @classmethod
    def _flags_known(cls, v: list[str]) -> list[str]:
        unknown = sorted(set(v) - ALLOWED_FLAGS)
        if unknown:
            raise ValueError(f"unknown flags: {unknown}")
        return v

    @model_validator(mode="after")
    def _enforce(self) -> "Verdict":
        valid = CLAIM_LABELS if self.axis == "claim" else MEDIA_LABELS
        if self.label not in valid:
            raise ValueError(f"label {self.label!r} is not allowed on the {self.axis} axis")

        fallback = "UNVERIFIABLE_YET" if self.axis == "claim" else "INCONCLUSIVE"
        conflict = self._trusted_conflict()
        if conflict or not self._supported_by_evidence():
            self.label = fallback
            self.confidence_band = "low"
            added = ["COERCED", "CONFLICT"] if conflict else ["COERCED"]
            self.flags = [*self.flags, *(f for f in added if f not in self.flags)]
        return self

    def _trusted_conflict(self) -> bool:
        """Bible 8.4: trusted support and trusted contradiction together -> UNVERIFIABLE_YET with CONFLICT."""
        if self.axis != "claim" or self.label not in CLAIM_DECISIVE:
            return False
        roles = {e.role for e in self.evidence if e.tier in TRUSTED_TIERS}
        return "supports" in roles and "contradicts" in roles

    def _supported_by_evidence(self) -> bool:
        ev = self.evidence
        label = self.label
        if self.axis == "claim":
            if label not in CLAIM_DECISIVE:
                return True                                   # UNVERIFIABLE_YET and NOT_A_CLAIM need nothing
            # D35: only Tier 1 or 2 records can decide, so the deciding roles must come from those records.
            roles = {e.role for e in ev if e.tier in TRUSTED_TIERS}
            if label == "SUPPORTED":
                return "supports" in roles
            if label == "REFUTED":
                return "contradicts" in roles                 # D12: absence of support never refutes
            return "supports" in roles and "context" in roles  # MISSING_CONTEXT shows both sides
        if label not in MEDIA_DECISIVE:
            return True
        if label == "OUT_OF_CONTEXT":
            return any(e.role == "earlier_appearance" for e in ev)
        if label == "AUTHENTIC_IN_CONTEXT":
            # I11: finding nothing is not authenticity. Needs positive provenance evidence.
            return any(e.route == "provenance" and e.role == "supports" for e in ev)
        return bool(ev)                                       # EDITED, SYNTHETIC need some evidence


class CorrectionCard(_Model):
    """What the bot sends back (Bible D47, I16). Carries the answer and a source, never the original media."""

    verdict_id: str
    lang: str
    headline: str = Field(min_length=1, max_length=140)
    sentence: str = Field(min_length=1, max_length=400)
    links: list[str] = Field(min_length=1)
    checked_at: datetime

    @field_validator("links")
    @classmethod
    def _https(cls, v: list[str]) -> list[str]:
        if not all(link.startswith("https://") for link in v):  # forge: allow-literal scheme check, not a source
            raise ValueError("every link must be https")
        return v


class QualityReport(_Model):
    passed: bool
    reasons: list[str] = Field(default_factory=list)
    measures: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reasons_when_failed(self) -> "QualityReport":
        if not self.passed and not self.reasons:
            raise ValueError("a failed quality report must say why")
        return self


class Segment(_Model):
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    score: float | None = None


class DetectorResult(_Model):
    detector: str
    modality: Literal["audio", "image", "video"]
    version: str
    abstained: bool
    score: float | None = None
    p_calibrated: float | None = Field(default=None, ge=0.0, le=1.0)
    quality: QualityReport
    segments: list[Segment] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    latency_ms: int = Field(default=0, ge=0)
    degraded: bool = False

    @model_validator(mode="after")
    def _gate_means_no_score(self) -> "DetectorResult":
        if not self.quality.passed and not self.abstained:
            raise ValueError("a detector that failed its quality gate must abstain (Bible I2)")
        if self.abstained and (self.score is not None or self.p_calibrated is not None):
            raise ValueError("an abstaining detector must not carry a score (Bible I2)")
        if not self.abstained and self.score is None:
            raise ValueError("a detector that answered must carry a score")
        return self


class VariantEdge(_Model):
    variant_id: str
    claim_id: str
    first_seen_ts: datetime
    text: str
    lang: str


class LedgerEntry(_Model):
    seq: int = Field(ge=1)
    prev_hash: str
    hash: str
    created_at: datetime
    kind: str
    payload_sha256: str
    payload: dict
    code_version: str
    idem_key: str | None = None
