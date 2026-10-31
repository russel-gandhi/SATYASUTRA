"""Scam Guard on the labelled set (package SG5): detection rate by type, chunks until the alert, and the false-alarm rate on ordinary calls.

    python scripts/guard_eval.py --out docs/guard-sheet.md

Runs the plain rules on text (no audio, no model), so the numbers describe the rules on the scripts we wrote, not real calls, accents or transcript errors.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "eval"))

from lib import pct  # noqa: E402
from satyasutra.scam.script import Call, load_script  # noqa: E402

SET = ROOT / "tests" / "guard" / "set.yaml"


def run_item(rules, item) -> dict:
    call, first_red, first_amber, states = Call(rules), None, None, []
    for i, chunk in enumerate(item["chunks"], start=1):
        v = call.add(chunk)
        states.append(v.state)
        if v.state == "scam" and first_red is None:
            first_red = i
        if v.state != "listening" and first_amber is None:
            first_amber = i
    return {"id": item["id"], "kind": item["kind"], "type": item["type"], "lang": item["lang"], "red_at": first_red, "amber_at": first_amber, "chunks": len(item["chunks"])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/guard-sheet.md")
    args = ap.parse_args()
    rules = load_script()
    items = yaml.safe_load(SET.read_text(encoding="utf-8"))["items"]
    res = [run_item(rules, i) for i in items]
    scams, ordinary = [r for r in res if r["kind"] == "scam"], [r for r in res if r["kind"] == "ordinary"]
    caught = [r for r in scams if r["red_at"]]
    by_type = defaultdict(list)
    for r in scams:
        by_type[r["type"]].append(r)
    by_lang = defaultdict(list)
    for r in scams:
        by_lang[r["lang"]].append(r)
    red_o, amber_o = [r for r in ordinary if r["red_at"]], [r for r in ordinary if r["amber_at"] and not r["red_at"]]
    lines = ["# Scam Guard sheet", "", "Command: `python scripts/guard_eval.py`. Text only, plain rules, no audio. " + f"{len(scams)} scam scripts and {len(ordinary)} ordinary calls.", "",
             "| Metric | Result (n, 95% range) |", "|---|---|",
             f"| Scam scripts that reached red | {pct(len(caught), len(scams))} |",
             f"| Ordinary calls that reached red (false alarm) | {pct(len(red_o), len(ordinary))} |",
             f"| Ordinary calls that reached amber only | {pct(len(amber_o), len(ordinary))} |",
             f"| Chunks until red on caught scams (median, slowest) | {statistics.median(r['red_at'] for r in caught):g}, {max(r['red_at'] for r in caught)} |" if caught else "| Chunks until red | n/a |",
             "", "## Detection by type", "", "| Type | Caught |", "|---|---|"]
    lines += [f"| {t} | {pct(sum(1 for r in rs if r['red_at']), len(rs))} |" for t, rs in sorted(by_type.items())]
    lines += ["", "## Detection by language", "", "| Language | Caught |", "|---|---|"]
    lines += [f"| {t} | {pct(sum(1 for r in rs if r['red_at']), len(rs))} |" for t, rs in sorted(by_lang.items())]
    lines += ["", "## Every miss and every false alarm", ""]
    for r in scams:
        if not r["red_at"]:
            lines.append(f"- MISSED scam `{r['id']}` ({r['type']}, {r['lang']})")
    for r in red_o:
        lines.append(f"- FALSE ALARM on `{r['id']}` ({r['type']}, {r['lang']}) at chunk {r['red_at']}")
    lines += ["", "## Limits", "",
              "- The rules and this set were written by the same people at the same time, and the rules were adjusted after the first run missed scripts in this same set (first run 11/16 caught; added threat phrases and two red combinations). These numbers are optimistic. No clean held-out part exists.",
              "- Text only: real calls come through a speaker and a transcriber that makes errors (especially in Hindi and Hinglish). Not measured here; see package SG6.",
              "- One labeller. Hindi and Hinglish phrases and scripts are our drafts, not reviewed by a native speaker (SG7).",
              "- The delivery-OTP call (n16) is a real gray case the rules cannot tell from a scam; it is kept on purpose."]
    out = ROOT / args.out
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
