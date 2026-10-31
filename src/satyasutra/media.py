"""Media check for one uploaded image: has it appeared in a fact-check, and does a forensic model see manipulation?

Two independent signals, never merged into one score:
  - provenance: the image's fingerprint against our debunked-image index. A match means "appeared in a fact-checker's article";
    it is a lead, not a verdict on any claim.
  - forensics: TruFor (a separate local service) scores whether the image was edited (spliced, copy-moved, retouched). It does not
    detect fully AI-generated images, and the result says so. Below the calibrated cut the label is INCONCLUSIVE, never
    "authentic": a detector that finds nothing has not shown the image is genuine.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config import Config
from .errors import ConfigError
from .explain import explain_media
from .provenance import BadImage, ImageIndex, fingerprint

SCOPE_NOTE = "TruFor finds edited images (splicing, copy-move, retouching). It does not detect fully AI-generated images."
AI_NOTE = ("The AI-generated score comes from a separate model (Smaug). It can miss new or evasive generators, and a real photo with a small AI-edited "
           "region can read as real; its cut was measured on 80 Commons images only.")


@dataclass
class MediaResult:
    width: int
    height: int
    matches: list[dict]
    forensics: dict
    label: str
    reasons: list[str] = field(default_factory=list)
    explanation: dict = field(default_factory=dict)


def label_from(edited: bool, ai_generated: bool) -> str:
    """The label rule alone: a lone AI-image detector hit outranks an edit hit, and no hit is INCONCLUSIVE, never authentic."""
    return "SYNTHETIC_SUSPECTED" if ai_generated else ("EDITED" if edited else "INCONCLUSIVE")


def forensic_scores(cfg: Config, data: bytes, client: httpx.Client | None = None) -> dict:
    """Ask the TruFor service about the image. An unreachable service is reported, not hidden and not guessed around."""
    settings = cfg.runtime.forensics
    http = client or httpx.Client(timeout=settings.timeout_s)
    try:
        r = http.post(f"{settings.url}/analyze", content=data, headers={"Content-Type": "application/octet-stream"})
    except httpx.HTTPError as exc:
        return {"available": False, "reason": f"forensics service unreachable ({type(exc).__name__})"}
    finally:
        if client is None:
            http.close()
    if r.status_code != httpx.codes.OK:
        return {"available": False, "reason": f"forensics service refused the image (HTTP {r.status_code})"}
    body = r.json()
    body["available"] = True
    return body


def check_media(cfg: Config, data: bytes, index: ImageIndex, analyse=forensic_scores) -> MediaResult:
    try:
        fp = fingerprint(data)
    except BadImage as exc:
        raise ValueError(str(exc))
    cut = cfg.thresholds.thresholds["image_match_pdq_distance"].value
    hits = index.nearest(fp, cut, cfg.runtime.provenance.match_limit)
    matches = [{
        "distance": h.distance, "exact": h.exact, "mirrored": h.mirrored, "publisher": h.source.publisher, "lang": h.source.lang,
        "title": h.source.title, "article_url": h.source.article_url, "labels": h.source.labels,
        "reviewed_at": h.source.reviewed_at.isoformat() if h.source.reviewed_at else None,
    } for h in hits]

    forensics = analyse(cfg, data)
    reasons: list[str] = []
    label = "INCONCLUSIVE"
    if not forensics.get("available"):
        reasons.append(forensics.get("reason", "forensics unavailable"))
    else:
        name = cfg.runtime.forensics.threshold
        entry = cfg.thresholds.thresholds.get(name)
        score = forensics.get("score")
        if entry is None or score is None:
            reasons.append(f"no calibrated {name}: the score is shown but no label is given")
        elif score >= entry.value:
            label = "EDITED"
            reasons.append(f"TruFor manipulation score {score:.2f} is at or above the cut {entry.value}; the heatmap marks where")
        else:
            reasons.append(f"TruFor manipulation score {score:.2f} is below the cut {entry.value}: no editing found, which does not prove the image is genuine")
        ai_score = forensics.get("ai_score")
        ai_entry = cfg.thresholds.thresholds.get(cfg.runtime.forensics.ai_threshold)
        if ai_score is None:
            reasons.append(SCOPE_NOTE + (f" (AI-image detector unavailable: {forensics['ai_error']})" if forensics.get("ai_error") else ""))
        elif ai_entry is None:
            reasons.append(f"AI-generated score {ai_score:.2f}; no calibrated cut, so no label is given from it. " + AI_NOTE)
        elif ai_score >= ai_entry.value:
            label = "SYNTHETIC_SUSPECTED"       # one detector alone is a suspicion, not the SYNTHETIC finding; no second detector corroborates it
            reasons.append(f"AI-generated score {ai_score:.2f} is at or above the cut {ai_entry.value}: the image looks AI-generated, but one detector alone gives a suspicion, not a finding. " + AI_NOTE)
        else:
            reasons.append(f"AI-generated score {ai_score:.2f} is below the cut {ai_entry.value}: no sign of AI generation found, which does not prove it is a real photo. " + AI_NOTE)
    if matches:
        reasons.append(f"appeared in {len(matches)} fact-checker article(s): a lead about its history, not a verdict on any claim")
    else:
        reasons.append("not in the debunked-image index; that says nothing about whether it is authentic")
    explanation = explain_media(cfg, forensics, matches, label, label_from) if forensics.get("available") else {}
    return MediaResult(fp.width, fp.height, matches, forensics, label, reasons, explanation)
