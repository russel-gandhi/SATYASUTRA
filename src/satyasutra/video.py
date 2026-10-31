"""Video check: sample frames with ffmpeg, then run each through the same two image signals as /media/check.

What this can and cannot say, and the result always says it:
  - Each sampled frame is scored by TruFor (editing: splicing, copy-move, retouching) and looked up in the debunked-image index.
  - It does NOT detect face swaps or AI-generated video, which leave no splice for TruFor to find, and it looks at a handful of
    frames, not every frame. A video with no flagged frame is INCONCLUSIVE, never "authentic".
  - One flagged frame is enough to call the video EDITED, and the result says how many of the sampled frames were flagged.
ffmpeg and ffprobe are run as plain argument lists (no shell) on a temporary copy of the upload that is deleted afterwards.
"""

from __future__ import annotations

import base64
import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from .config import Config
from .media import SCOPE_NOTE, forensic_scores
from .provenance import BadImage, ImageIndex, fingerprint

FFMPEG, FFPROBE = "ffmpeg", "ffprobe"
VIDEO_SCOPE_NOTE = "Video deepfakes (face swaps, AI-generated video) are not detected: only editing inside the sampled frames is."
COMPRESSION_NOTE = ("Video compression hides editing traces: in our test (scripts/video_live.py) a spliced frame that scored 1.00 as a still image scored 0.27 "
                    "after H.264 encoding, so a clean result here is weak evidence.")
THUMB_QUALITY = 70


@dataclass
class FrameResult:
    index: int
    at_s: float
    score: float | None
    flagged: bool
    thumb_jpeg_b64: str
    matches: list[dict] = field(default_factory=list)
    heatmap_png_b64: str | None = None
    note: str = ""


@dataclass
class VideoResult:
    duration_s: float
    frames: list[FrameResult]
    label: str
    reasons: list[str]
    forensics_available: bool


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"{name} is not installed or not on PATH")
    return path


def probe_duration(path: Path, timeout_s: float) -> float:
    out = subprocess.run([_tool(FFPROBE), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True, timeout=timeout_s, check=False)
    try:
        duration = float(out.stdout.strip())
    except ValueError:
        raise ValueError("not a readable video") from None
    if duration <= 0:
        raise ValueError("the video has no duration")
    return duration


def grab_frame(path: Path, at_s: float, timeout_s: float) -> bytes | None:
    out = subprocess.run([_tool(FFMPEG), "-v", "error", "-ss", f"{at_s:.3f}", "-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "2", "pipe:1"],
                         capture_output=True, timeout=timeout_s, check=False)
    return out.stdout or None


def _thumb(data: bytes, width: int) -> str:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img = img.resize((width, max(1, round(img.height * width / img.width))))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=THUMB_QUALITY)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def check_video(cfg: Config, data: bytes, index: ImageIndex, analyse: Callable[[Config, bytes], dict] = forensic_scores) -> VideoResult:
    s = cfg.runtime.forensics
    cut_entry = cfg.thresholds.thresholds.get(s.threshold)
    match_cut = cfg.thresholds.thresholds["image_match_pdq_distance"].value
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / "upload"
        path.write_bytes(data)
        duration = probe_duration(path, s.ffmpeg_timeout_s)
        if duration > s.max_video_s:
            raise ValueError(f"the video is {duration:.0f} s long; the limit is {s.max_video_s:.0f} s")
        frames: list[FrameResult] = []
        for i in range(s.video_frames):
            at = duration * (i + 0.5) / s.video_frames
            jpg = grab_frame(path, at, s.ffmpeg_timeout_s)
            if not jpg:
                frames.append(FrameResult(i, round(at, 2), None, False, "", note="no frame could be read here"))
                continue
            forensics = analyse(cfg, jpg)
            score = forensics.get("score") if forensics.get("available") else None
            try:
                hits = index.nearest(fingerprint(jpg), match_cut, cfg.runtime.provenance.match_limit)
            except BadImage:
                hits = []
            frames.append(FrameResult(
                i, round(at, 2), score, bool(cut_entry and score is not None and score >= cut_entry.value), _thumb(jpg, s.thumb_width),
                [{"distance": h.distance, "publisher": h.source.publisher, "title": h.source.title, "article_url": h.source.article_url} for h in hits],
                forensics.get("heatmap_png_b64"), "" if forensics.get("available") else forensics.get("reason", "forensics unavailable"),
            ))

    scored = [f for f in frames if f.score is not None]
    flagged = [f for f in frames if f.flagged]
    reasons: list[str] = []
    label = "INCONCLUSIVE"
    if not scored:
        reasons.append(next((f.note for f in frames if f.note), "no frame could be analysed"))
    elif cut_entry is None:
        reasons.append(f"no calibrated {s.threshold}: scores are shown but no label is given")
    elif flagged:
        label = "EDITED"
        top = max(flagged, key=lambda f: f.score)
        reasons.append(f"{len(flagged)} of {len(scored)} sampled frames are at or above the cut {cut_entry.value} (highest {top.score:.2f} at {top.at_s:.1f} s); the heatmap marks where")
    else:
        best = max(scored, key=lambda f: f.score)
        reasons.append(f"none of {len(scored)} sampled frames reaches the cut {cut_entry.value} (highest {best.score:.2f}): no editing found in those frames, which does not prove the video is genuine")
    reasons += [SCOPE_NOTE, VIDEO_SCOPE_NOTE, COMPRESSION_NOTE]
    seen = {m["article_url"] for f in frames for m in f.matches}
    reasons.append(f"frames appeared in {len(seen)} fact-checker article(s): a lead about history, not a verdict on any claim" if seen
                   else "no sampled frame is in the debunked-image index; that says nothing about whether the video is authentic")
    return VideoResult(round(duration, 2), frames, label, reasons, bool(scored))
