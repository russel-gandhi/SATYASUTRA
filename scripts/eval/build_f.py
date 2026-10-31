"""Collect candidate FALSE claims from published fact-checks (group F), for two people to review.

    python scripts/eval/build_f.py [--seeds tests/eval/seeds_f.txt] [--per-seed 20] [--lang en,hi]

It asks the Google Fact Check Tools API (the same source the agent uses, key GOOGLE_FACTCHECK_API_KEY in .env) about each seed topic and keeps
reviews whose rating the system's own rating map reads as "refutes" (false, fake, misleading ...). The claim text is the one the fact-checker
wrote, not our paraphrase. Output: tests/eval/f_candidates.jsonl, every row marked pending.

This is the one group that needs a key and human review. After it runs:
  1. Two people read every candidate separately against its review URL and keep only claims that are (a) checkable as text, (b) not about a
     photo or video, (c) really rated false by the checker, and (d) clearly worded. They write their names in labelled_by.
  2. Rows both accepted go into tests/eval/f_rows.jsonl with review_status "accepted" (and id, group "F", text, truth "false", source_url,
     source_date, source_title, labelled_by, lang). A row only one person accepted stays out.
  3. Run scripts/eval/assemble.py again; the F rows join the dataset. Run F with --factcheck off (otherwise the system finds the very review
     the claim came from) and again with --factcheck on to see coverage when a fact-checker exists.
Terms: only the claim text, publisher, rating, date and URL are stored, never article bodies or images.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import EVAL_DIR, ROOT, normalise, sha, write_jsonl  # noqa: E402

CANDIDATES = EVAL_DIR / "f_candidates.jsonl"
SEEDS = EVAL_DIR / "seeds_f.txt"


def candidates_from(body: dict, seed: str, lang: str, refutes) -> list[dict]:
    """Rows for reviews the rating map reads as 'refutes'. `refutes(rating) -> bool` is injected so this can be tested without config."""
    out = []
    for claim in body.get("claims", []) or []:
        text = (claim.get("text") or "").strip()
        for review in claim.get("claimReview", []) or []:
            rating, url = (review.get("textualRating") or "").strip(), (review.get("url") or "").strip()
            if not (text and rating and url and refutes(rating)):
                continue
            out.append({
                "id": "fc-" + sha(normalise(text))[:8], "group": "F", "text": text, "lang": review.get("languageCode") or lang, "truth": "false",
                "claimant": claim.get("claimant", ""), "publisher": (review.get("publisher") or {}).get("name", ""), "rating": rating,
                "source_url": url, "source_title": review.get("title", ""), "source_date": (review.get("reviewDate") or "")[:10], "seed": seed,
                "review_status": "pending", "labelled_by": [],
            })
            break                                           # one review per claim is enough for a label
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", default=str(SEEDS))
    ap.add_argument("--per-seed", type=int, default=20)
    ap.add_argument("--lang", default="en,hi")
    ap.add_argument("--base-url", default="", help="override the API address (tests only)")
    a = ap.parse_args(argv)

    from satyasutra.claims.rules import classify_rating
    from satyasutra.config import load_config
    from satyasutra.envfile import load_env_file
    from satyasutra.sources.probe import build_user_agent

    cfg = load_config()
    load_env_file(ROOT / ".env")
    src = cfg.source("factcheck_tools")
    key = os.environ.get(src.auth_env or "", "")
    if not key:
        print(f"set {src.auth_env} in .env to collect candidates", file=sys.stderr)
        return 2
    base = a.base_url or src.probe_url.split("?")[0]
    seeds = [s.strip() for s in Path(a.seeds).read_text(encoding="utf-8").splitlines() if s.strip() and not s.startswith("#")]
    refutes = lambda rating: classify_rating(rating, cfg) == "refutes"
    seen: dict[str, dict] = {}
    with httpx.Client(timeout=cfg.runtime.probe.timeout_s * 2, headers={"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}) as http:
        for seed in seeds:
            for lang in a.lang.split(","):
                try:
                    r = http.get(base, params={"query": seed, "languageCode": lang, "pageSize": a.per_seed, src.auth_param or "key": key})
                    body = r.json() if r.status_code == httpx.codes.OK else {}
                except (httpx.HTTPError, ValueError) as exc:
                    print(f"{seed!r} {lang}: failed ({type(exc).__name__})")        # class only: the message can echo the key
                    continue
                rows = candidates_from(body, seed, lang, refutes)
                for row in rows:
                    seen.setdefault(normalise(row["text"]), row)
                print(f"{seed!r} {lang}: {len(rows)} candidate(s)")
    write_jsonl(CANDIDATES, sorted(seen.values(), key=lambda r: r["id"]))
    print(f"{len(seen)} unique candidates -> {CANDIDATES} (all pending review)")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
