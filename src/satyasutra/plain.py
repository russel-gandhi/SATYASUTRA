"""Plain-language headlines and reasons for image, video and voice checks.

The labels (EDITED, SYNTHETIC_SUSPECTED, INCONCLUSIVE) stay as they are: the ledger, the report and the evaluation read them. This module only writes what a person
reads: how strong the signal is in everyday words, and why, with no model names, scores or cut-offs. We detect edited pictures and AI-made pictures and voices.
We do not detect face swaps, so nothing here says "deepfake".
"""

from __future__ import annotations


def _of(n: int, total: int, what: str) -> str:
    return f"{n} of {total} frames {what}"


def image(edited: bool, ai: bool, available: bool, matched: int) -> tuple[str, list[str]]:
    if not available:
        return "Could not check this picture", []
    if edited and ai:
        head = "Likely edited and AI-made"
    elif edited:
        head = "Likely edited"
    elif ai:
        head = "Likely AI-made"
    else:
        head = "No sign of editing or AI"
    why: list[str] = []
    if edited:
        why.append("Parts of this picture look changed after it was taken. The red glow shows where.")
    if ai:
        why.append("The look of this picture matches pictures made by AI tools.")
    if not edited and not ai:
        why.append("Neither the editing check nor the AI check found anything.")
    if matched:
        why.append("This picture has already appeared in a fact-check." if matched == 1 else f"This picture has already appeared in {matched} fact-checks.")
    return head, why


def video(edited_frames: int, ai_frames: int, ai_enough: bool, scored: int, ai_scored: int, mostly_share: float, worst_at_s: float | None, matched: int) -> tuple[str, list[str]]:
    if not scored:
        return "Could not check this video", []
    edited = edited_frames > 0
    ai = ai_enough
    mostly_edited = scored and edited_frames / scored > mostly_share
    mostly_ai = ai_scored and ai_frames / ai_scored > mostly_share
    if edited and ai:
        head = "Mostly edited and AI-made" if mostly_edited and mostly_ai else "Likely edited and AI-made"
    elif edited:
        head = "Mostly edited" if mostly_edited else "Some frames look edited"
    elif ai:
        head = "Mostly AI-made" if mostly_ai else "Some frames look AI-made"
    else:
        head = "No sign of editing or AI"
    why: list[str] = []
    if edited:
        at = f" The worst one is at {worst_at_s:.1f} s." if worst_at_s is not None else ""
        why.append(_of(edited_frames, scored, "look changed after filming.") + at + " The red glow shows where.")
    if ai:
        why.append(_of(ai_frames, ai_scored, "look AI-made."))
    if not edited and not ai:
        why.append("None of the frames we looked at showed editing or an AI look.")
    if matched:
        why.append("Some of these frames have already appeared in a fact-check.")
    return head, why


def voice(synthetic: bool, flagged: int, total: int) -> tuple[str, list[str]]:
    if synthetic:
        return "Likely an AI or cloned voice", [f"{flagged} of {total} stretches of this clip sound machine-made."]
    return "No sign of a machine-made voice", [f"Only {flagged} of {total} stretches of this clip sound machine-made."] if flagged else ["The voice did not sound machine-made."]
