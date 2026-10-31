"""Shared pieces of the verdict dataset tooling: reading rows, the dev/test split, number changes, scoring and intervals.

Everything here is plain code with no network and no model, so it can be tested without keys. The claim data itself lives in
tests/eval (recorded data may exist in tests/ only); nothing under src/ may contain a row's text.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "tests" / "eval"
CLAIMS = EVAL_DIR / "claims.jsonl"
SNAPSHOT = EVAL_DIR / "official_snapshot.jsonl"

ABSTAIN = ("UNVERIFIABLE_YET", "NOT_A_CLAIM", "NO_CLAIM")
DECIDED = ("REFUTED", "SUPPORTED", "MISSING_CONTEXT")
Z95 = 1.959964

# Per truth value: what counts as right and what counts as harmful. Anything else is an abstention (a valid answer, not a solved one).
RULES = {
    "false": {"expected": ["REFUTED"], "wrong": ["SUPPORTED"]},
    "true": {"expected": ["SUPPORTED"], "wrong": ["REFUTED"]},
    "unknown": {"expected": ["UNVERIFIABLE_YET", "NOT_A_CLAIM", "NO_CLAIM"], "wrong": ["REFUTED", "SUPPORTED", "MISSING_CONTEXT"]},
}


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8", newline="\n")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def root_id(row: dict) -> str:
    """The row a derived row comes from (a changed-number claim from its true claim, a translation from its original)."""
    return row.get("built_from") or row.get("base_id") or row["id"]


def split_of(row: dict, by_id: dict[str, dict] | None = None) -> str:
    """dev for one id in three, test for the rest, decided by hash so nobody can choose. A derived row follows its root, so a
    claim and its changed-number twin (same official passage) never land on different sides."""
    rid = row["id"]
    seen = set()
    while by_id and by_id.get(rid) and root_id(by_id[rid]) != rid and rid not in seen:
        seen.add(rid)
        rid = root_id(by_id[rid])
    return "dev" if int(sha(rid)[:8], 16) % 3 == 0 else "test"


def normalise(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold(), flags=re.UNICODE))


# ---- changing one number (the C group) ----------------------------------------------------------------------------------------

def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def perturb_number(token: str, kind: str) -> str:
    """A different value of the same kind, as a rumour might state it, in the same format (commas, decimals).
    percent: +1.5 points; amount: x1.5 rounded; count: +25. Deterministic, so the dataset rebuilds identically."""
    commas, dec = "," in token, _decimals(token)
    value = float(token.replace(",", ""))
    if kind == "percent":
        new = value + 1.5
    elif kind == "amount":
        new = round(value * 1.5)
    elif kind == "count":
        new = value + 25
    else:
        raise ValueError(f"unknown slot kind {kind!r}")
    text = f"{new:,.{dec}f}" if commas else f"{new:.{dec}f}"
    if text == token:
        raise ValueError(f"{token!r} did not change")
    return text


def change_slot(text: str, token: str, kind: str) -> tuple[str, str]:
    """Replace the one occurrence of `token` in `text`; returns (new text, new token). Refuses an ambiguous token."""
    if text.count(token) != 1:
        raise ValueError(f"slot {token!r} must appear exactly once in {text!r}")
    new = perturb_number(token, kind)
    return text.replace(token, new, 1), new


# ---- scoring ------------------------------------------------------------------------------------------------------------------

def wilson(k: int, n: int) -> tuple[float, float]:
    """95 percent Wilson interval for k of n; (0, 0) when n is 0. Small samples have wide ranges, and the report shows them."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + Z95 ** 2 / n
    centre = (p + Z95 ** 2 / (2 * n)) / d
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95 ** 2 / (4 * n ** 2)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def pct(k: int, n: int) -> str:
    if n == 0:
        return "n/a"
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({100 * k / n:.0f}%, {100 * lo:.0f} to {100 * hi:.0f}%)"


def outcome(row: dict, label: str) -> str:
    """correct, wrong or abstain for one prediction, from the row's expected and wrong sets."""
    expected = row.get("expected") or RULES[row["truth"]]["expected"]
    wrong = row.get("wrong") or RULES[row["truth"]]["wrong"]
    if label in expected:
        return "correct"
    if label in wrong:
        return "wrong"
    return "abstain"


def summarise(results: list[dict]) -> dict:
    """results: dicts with group, truth, label, band, outcome, seconds. Returns counts per group and the named metrics."""
    by_group: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        by_group[r["group"]][r["outcome"]] += 1
    n = lambda rows: len(rows)
    t = [r for r in results if r["truth"] == "true"]
    f = [r for r in results if r["truth"] == "false"]
    u = [r for r in results if r["truth"] == "unknown"]
    decided = [r for r in results if r["label"] in DECIDED]
    bands = {b: [r for r in decided if r["band"] == b] for b in ("high", "medium", "low")}
    seconds = sorted(r["seconds"] for r in results if r.get("seconds") is not None)
    pick = lambda q: seconds[min(len(seconds) - 1, int(q * len(seconds)))] if seconds else None
    return {
        "groups": {g: dict(c) | {"n": sum(c.values())} for g, c in sorted(by_group.items())},
        "false_refute": (sum(r["label"] == "REFUTED" for r in t), n(t)),
        "false_support": (sum(r["label"] == "SUPPORTED" for r in f), n(f)),
        "refute_recall": (sum(r["label"] == "REFUTED" for r in f), n(f)),
        "coverage": (len(decided), n(results)),
        "correct_abstention": (sum(r["outcome"] == "correct" for r in u), n(u)),
        "precision_by_band": {b: (sum(r["outcome"] == "correct" for r in rows), n(rows)) for b, rows in bands.items()},
        "seconds_median": pick(0.5), "seconds_p95": pick(0.95),
    }


# ---- guard against test rows leaking into code --------------------------------------------------------------------------------

def find_leaks(rows: list[dict], roots: list[Path], suffixes=(".py", ".yaml", ".yml", ".txt", ".md", ".json")) -> list[tuple[str, str]]:
    """(row id, file) pairs where a test row's claim text appears in a file the system reads or tunes on. Compared on normalised
    words, so a changed quote mark or case does not hide it."""
    wanted = {r["id"]: normalise(r["text"]) for r in rows if r.get("split") == "test" and len(normalise(r["text"])) > 12}
    hits: list[tuple[str, str]] = []
    for base in roots:
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes or "__pycache__" in path.parts:
                continue
            try:
                body = normalise(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            shown = str(path.relative_to(ROOT)) if ROOT in path.parents else str(path)
            hits += [(rid, shown) for rid, text in wanted.items() if text in body]
    return hits
