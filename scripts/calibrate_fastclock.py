"""Choose the near-duplicate similarity threshold from real posts (ticket F1-08).

    python scripts/calibrate_fastclock.py capture --seconds 300 --out <file.jsonl>     # live capture, written outside the repo
    python scripts/calibrate_fastclock.py analyse --from <file.jsonl>                  # histogram + pairs around the proposal
    python scripts/calibrate_fastclock.py write --from <file.jsonl> --value 0.6 --note "..."   # record it in config/thresholds.yaml

The proposal is the quietest bin of exact-Jaccard similarity between candidate pairs, between the lowest and highest bins that
hold pairs. The sample pairs it prints are what a human checks before `write`. Captured posts are never committed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from datetime import date
from itertools import combinations
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.fastclock.minhash import MinHasher, exact_jaccard  # noqa: E402
from satyasutra.fastclock.normalise import normalise, shingles  # noqa: E402
from satyasutra.ingest import IngestStats, author_salt, stream_posts  # noqa: E402

BIN = 0.1
CANDIDATE_ROWS = 2            # permissive banding for finding pairs: catches similarity from roughly 0.1 up
MAX_BUCKET = 200              # a bucket this big is boilerplate; skip it rather than build millions of pairs
SAMPLE_PER_SIDE = 8
PREVIEW = 100


async def capture(seconds: float, out: Path) -> int:
    cfg = load_config().runtime.ingest
    stats, n = IngestStats(), 0
    deadline = time.monotonic() + seconds

    async def run():
        nonlocal n
        with out.open("w", encoding="utf-8") as fh:
            async for post in stream_posts(cfg, stats, author_salt()):
                fh.write(json.dumps({"id": post.id, "text": post.text}, ensure_ascii=False) + "\n")
                n += 1

    try:
        await asyncio.wait_for(run(), seconds)
    except asyncio.TimeoutError:
        pass
    return n


def load(path: Path) -> list[dict]:
    # split on the newline character only: post text can hold U+2028 and friends, which str.splitlines() would cut through
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split(chr(10)) if line.strip()]


def candidate_pairs(posts: list[dict], k: int, num_perm: int):
    """Unique normalised texts, their shingles, and the LSH-candidate pairs between them."""
    counts: dict[str, int] = defaultdict(int)
    for p in posts:
        counts[normalise(p["text"])] += 1
    texts = [t for t in counts if shingles(t, k)]
    grams = {t: shingles(t, k) for t in texts}
    hasher = MinHasher(num_perm)
    sigs = {t: hasher.signature(grams[t]) for t in texts}
    buckets: dict[tuple, list[str]] = defaultdict(list)
    for t, sig in sigs.items():
        for b in range(num_perm // CANDIDATE_ROWS):
            buckets[(b, sig[b * CANDIDATE_ROWS:(b + 1) * CANDIDATE_ROWS])].append(t)
    seen, pairs = set(), []
    for members in buckets.values():
        if len(members) > MAX_BUCKET:
            continue
        for a, b in combinations(members, 2):
            if (a, b) in seen or (b, a) in seen:
                continue
            seen.add((a, b))
            pairs.append((exact_jaccard(grams[a], grams[b]), a, b))
    return counts, pairs


def histogram(pairs) -> dict[int, int]:
    h: dict[int, int] = defaultdict(int)
    for sim, _, _ in pairs:
        h[min(int(sim / BIN), int(1 / BIN) - 1)] += 1
    return h


def propose(h: dict[int, int]) -> float | None:
    """The quietest bin strictly between the lowest and highest populated bins, as its centre; None if there is no interior."""
    populated = sorted(i for i, c in h.items() if c)
    if len(populated) < 3:
        return None
    interior = range(populated[0] + 1, populated[-1])
    quietest = min(interior, key=lambda i: (h.get(i, 0), -i))
    return round((quietest + 0.5) * BIN, 2)


def show(label: str, rows) -> None:
    print(f"\n{label}")
    for sim, a, b in rows:
        print(f"  {sim:.2f}\n    A: {a[:PREVIEW]}\n    B: {b[:PREVIEW]}")


def analyse(path: Path) -> int:
    cfg = load_config().runtime.fastclock
    posts = load(path)
    counts, pairs = candidate_pairs(posts, cfg.shingle_words, cfg.num_perm)
    repeats = sum(c - 1 for c in counts.values())
    print(f"{len(posts)} posts, {len(counts)} distinct normalised texts; exact repeats: {repeats} ({repeats / max(len(posts), 1):.1%} of posts)")
    print(f"{len(pairs)} candidate pairs. exact Jaccard histogram:")
    h = histogram(pairs)
    for i in range(int(1 / BIN)):
        print(f"  {i * BIN:.1f}-{(i + 1) * BIN:.1f}  {h.get(i, 0):6d}  {'#' * min(h.get(i, 0) // 5, 60)}")
    value = propose(h)
    print(f"\nproposed threshold (quietest interior bin centre): {value}")
    print(f"LSH bucketing in config catches from about {(1 / cfg.lsh_bands) ** (cfg.lsh_bands / cfg.num_perm):.2f} (formula (1/bands)^(1/rows)); the threshold should not sit far below that")
    if value is not None:
        above = sorted((p for p in pairs if p[0] >= value), key=lambda p: p[0])[:SAMPLE_PER_SIDE]
        below = sorted((p for p in pairs if p[0] < value), key=lambda p: -p[0])[:SAMPLE_PER_SIDE]
        show(f"just ABOVE {value} (should be the same message):", above)
        show(f"just BELOW {value} (should be different messages):", below)
    return 0


def write(value: float, note: str, path: Path) -> int:
    target = ROOT / "config" / "thresholds.yaml"
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    data["thresholds"] = (data.get("thresholds") or {}) | {
        "cluster_jaccard": {"value": value, "calibrated_on": f"{path.name}: {note}", "date": date.today().isoformat()}
    }
    header = "".join(line for line in target.read_text(encoding="utf-8").splitlines(keepends=True) if line.startswith("#"))
    target.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"wrote cluster_jaccard={value} to {target.relative_to(ROOT)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture"); c.add_argument("--seconds", type=float, default=300); c.add_argument("--out", type=Path, required=True)
    a = sub.add_parser("analyse"); a.add_argument("--from", dest="src", type=Path, required=True)
    w = sub.add_parser("write"); w.add_argument("--from", dest="src", type=Path, required=True); w.add_argument("--value", type=float, required=True); w.add_argument("--note", required=True)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    if args.cmd == "capture":
        n = asyncio.run(capture(args.seconds, args.out))
        print(f"captured {n} posts to {args.out}")
        return 0 if n else 1
    if args.cmd == "analyse":
        return analyse(args.src)
    return write(args.value, args.note, args.src)


if __name__ == "__main__":
    raise SystemExit(main())
