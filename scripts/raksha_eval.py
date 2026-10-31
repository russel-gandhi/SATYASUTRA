"""Recognition of the private-images intent on the labelled set (package RK6).

    python scripts/raksha_eval.py --out docs/raksha-sheet.md

Runs the plain word-list matcher (act.plan.is_intimate), the same one the bot and the API use. Reports the triggered share and false triggers with ranges.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "eval"))

from lib import pct  # noqa: E402
from satyasutra.act.plan import is_intimate, load_actions  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/raksha-sheet.md")
    args = ap.parse_args()
    a = load_actions()
    items = yaml.safe_load((ROOT / "tests" / "raksha" / "set.yaml").read_text(encoding="utf-8"))["items"]
    pos, neg = [i for i in items if i["trigger"]], [i for i in items if not i["trigger"]]
    hit = [i for i in pos if is_intimate(i["text"], a)]
    false = [i for i in neg if is_intimate(i["text"], a)]
    lines = ["# Raksha recognition sheet", "", f"Command: `python scripts/raksha_eval.py`. {len(pos)} messages that should trigger, {len(neg)} near-misses that must not.", "",
             "| Metric | Result (n, 95% range) |", "|---|---|", f"| Messages that should trigger and did | {pct(len(hit), len(pos))} |", f"| Near-misses that triggered (false triggers) | {pct(len(false), len(neg))} |", "",
             "## Misses and false triggers", ""]
    lines += [f"- MISSED `{i['id']}`: {i['text']}" for i in pos if i not in hit] + [f"- FALSE TRIGGER `{i['id']}`: {i['text']}" for i in false]
    lines += ["", "## Limits", "", "- Written by us, one labeller, and the word list was written by the same people (optimistic). The first run caught 16/20; phrases were added after seeing the misses on this same set. Short, indirect, misspelt and other-language wording will be missed.",
              "- A miss means the bot treats the message like any other: the person is not harmed by a missed trigger but gets no help; the `/help_me` command always works.",
              "- No 'are you asking about private images?' question exists yet for unsure messages (the plan asked for one)."]
    (ROOT / args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
