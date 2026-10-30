"""Build the evidence-based parts of the verdict rules from real Fact Check responses (ticket F1-07).

    python scripts/calibrate_factcheck.py collect --queries docs/calibration/relevance_queries.txt --out <file.jsonl>
    python scripts/calibrate_factcheck.py ratings --from <file.jsonl>      # every distinct rating seen, with counts, for the rating table
    python scripts/calibrate_factcheck.py relevance --from <file.jsonl>    # similarity between each query and the claim a review is about
    python scripts/calibrate_factcheck.py write --value 0.3 --note "..."   # record factcheck_relevance in config/thresholds.yaml

Queries are claim-like sentences; the API returns reviews of similar claims, so what matters is how similar. A human reads the
sample pairs around the proposed cut before `write`.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.cache import TTLCache  # noqa: E402
from satyasutra.claims.rules import relevance  # noqa: E402
from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.fastclock.normalise import normalise  # noqa: E402
from satyasutra.sources import FactCheckClient  # noqa: E402

BIN = 0.1
SAMPLE_PER_SIDE = 8
PREVIEW = 100
NEWLINE = chr(10)


def collect(queries: Path, out: Path) -> int:
    cfg = load_config()
    lines = [q.strip() for q in queries.read_text(encoding="utf-8").split(NEWLINE) if q.strip()]
    rows = 0
    with tempfile.TemporaryDirectory() as tmp, out.open("w", encoding="utf-8") as fh:
        client = FactCheckClient(cfg, TTLCache(Path(tmp) / "c.sqlite3"))
        for q in lines:
            result = client.search(q)
            print(f"{len(result.evidence):2d} reviews  {q[:70]}", flush=True)
            for ev in result.evidence:
                fh.write(json.dumps({"query": q, "reviewed_claim": ev.reviewed_claim, "rating": ev.rating, "publisher": ev.publisher}, ensure_ascii=False) + NEWLINE)
                rows += 1
    print(f"{rows} (query, review) pairs from {len(lines)} queries written to {out}")
    return 0 if rows else 1


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split(NEWLINE) if line.strip()]


def ratings(path: Path) -> int:
    rows = load(path)
    counts = Counter(normalise(r["rating"]) for r in rows)
    publishers = defaultdict(set)
    for r in rows:
        publishers[normalise(r["rating"])].add(r["publisher"])
    print(f"{len(rows)} reviews, {len(counts)} distinct normalised ratings. most common first:")
    for rating, n in counts.most_common():
        print(f"  {n:3d}  {len(publishers[rating]):2d} publishers  {rating[:110]}")
    return 0


def relevance_report(path: Path) -> int:
    rows = load(path)
    scored = sorted(((relevance(r["query"], r["reviewed_claim"]), r) for r in rows), key=lambda x: x[0])
    hist = Counter(min(int(s / BIN), int(1 / BIN) - 1) for s, _ in scored)
    print(f"{len(scored)} pairs. relevance histogram:")
    for i in range(int(1 / BIN)):
        print(f"  {i * BIN:.1f}-{(i + 1) * BIN:.1f}  {hist.get(i, 0):4d}  {'#' * min(hist.get(i, 0), 60)}")
    print("\nPairs in ascending order of relevance, 1 in every few, so a human can find where 'same claim' begins:")
    step = max(1, len(scored) // (SAMPLE_PER_SIDE * 4))
    for s, r in scored[::step]:
        print(f"  {s:.2f}  Q: {r['query'][:PREVIEW]}{NEWLINE}        R: {r['reviewed_claim'][:PREVIEW]}")
    return 0


def write(value: float, note: str) -> int:
    target = ROOT / "config" / "thresholds.yaml"
    text = target.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    data["thresholds"] = (data.get("thresholds") or {}) | {
        "factcheck_relevance": {"value": value, "calibrated_on": note, "date": date.today().isoformat()}
    }
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    target.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"wrote factcheck_relevance={value} to {target.relative_to(ROOT)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect"); c.add_argument("--queries", type=Path, required=True); c.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("ratings"); r.add_argument("--from", dest="src", type=Path, required=True)
    v = sub.add_parser("relevance"); v.add_argument("--from", dest="src", type=Path, required=True)
    w = sub.add_parser("write"); w.add_argument("--value", type=float, required=True); w.add_argument("--note", required=True)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    if args.cmd == "collect":
        return collect(args.queries, args.out)
    if args.cmd == "ratings":
        return ratings(args.src)
    if args.cmd == "relevance":
        return relevance_report(args.src)
    return write(args.value, args.note)


if __name__ == "__main__":
    raise SystemExit(main())
