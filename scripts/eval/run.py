"""Run the verdict dataset through the claim agent and write the claim sheet.

    python scripts/eval/run.py --split dev --factcheck off --official snapshot --out docs/claim-sheet.md
    python scripts/eval/run.py --split dev --limit 10            # quick check while developing

It runs the same agent the web page streams (GET /agent/stream), which is the path that includes the official-text contrast
check. (POST /verify, which the Telegram bot calls today, does not run that check; see tests/eval/README.md.)

  --factcheck off   the Fact Check lookup returns nothing, so the system cannot find the review a claim came from
  --official snapshot | live | off   snapshot: contrast runs against tests/eval/official_snapshot.jsonl in a private index; live: the real index
  --split dev | test   test needs a clean git tree, never uses claims that appear in src/, config/ or tests/unit/, and is for one frozen version

Needs the same keys as the app (GEMINI_API_KEY, GOOGLE_FACTCHECK_API_KEY) in .env. The Gemini free tier allows 15 requests per minute,
so a full run takes a while; every prediction is written to tests/eval/runs/ so a wrong answer can be inspected afterwards.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import CLAIMS, EVAL_DIR, ROOT, SNAPSHOT, find_leaks, outcome, pct, read_jsonl, summarise, wilson  # noqa: E402

PICK_ORDER = ["REFUTED", "MISSING_CONTEXT", "SUPPORTED", "UNVERIFIABLE_YET", "NOT_A_CLAIM"]
LIMITS = """## Limits of these numbers

- The true and changed-number groups are built from official releases we can reach: today the RBI and SEBI feeds (PIB, MoHFW and cybercrime.gov.in
  answer 403). They describe **finance claims**, not general accuracy.
- Claims come from the team, one labeller for most groups. The labels of the T and C groups come from the release text and from the code that changed
  the number; U, R and the earlier sheet (B) still need an independent second look. Hindi rows are our own drafts, not reviewed by a native speaker.
- The set is small. Differences of a few claims are noise; the ranges show how wide the doubt is. It cannot show rare failures.
- Numbers describe one version of the code on one day. Fact-checks and news change, so a rerun on another day can differ.
"""


# ---- pure helpers (tested without keys) ---------------------------------------------------------------------------------------

def parse_result(sse: str) -> dict | None:
    """The JSON of the `result` event in an event-stream body, or None when the run produced none."""
    lines = sse.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "event: result":
            for nxt in lines[i + 1:i + 3]:
                if nxt.startswith("data:"):
                    return json.loads(nxt[len("data:"):].strip())
    return None


def top_claim(body: dict | None) -> dict:
    """The most decisive claim in a result, as label, band, reasons and the routes of its citations."""
    claims = (body or {}).get("claims") or []
    if not claims:
        return {"label": "NO_CLAIM", "band": "", "reasons": [], "routes": []}
    best = min(claims, key=lambda c: PICK_ORDER.index(c["label"]) if c["label"] in PICK_ORDER else len(PICK_ORDER))
    return {"label": best["label"], "band": best.get("confidence_band", ""), "reasons": best.get("reasons", [])[-3:],
            "routes": sorted({c.get("route", "") for c in best.get("citations", []) if c.get("route")})}


def classify_stream(sse: str) -> tuple[str, object]:
    """('result', body) when the agent answered; ('no_claim', None) when it finished without finding a checkable claim;
    ('error', detail) when a node failed (detail is the exception class the trace reports) or the stream just stopped."""
    body = parse_result(sse)
    if body is not None:
        return "result", body
    lines = sse.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "event: error":
            for nxt in lines[i + 1:i + 3]:
                if nxt.startswith("data:"):
                    try:
                        return "error", json.loads(nxt[len("data:"):].strip()).get("data", {}).get("type", "error")
                    except ValueError:
                        return "error", "error"
            return "error", "error"
    if any(line.strip() == "event: run_finished" for line in lines):
        return "no_claim", None
    return "error", "stream ended without a result: " + " ".join(sse[-120:].split())


def run_rows(rows: list[dict], runner, sleep=time.sleep, retries: int = 2, wait_s: float = 30.0) -> list[dict]:
    """runner(text) -> (sse text, seconds). A run that errors is retried (the model may have been at its limit), then recorded as ERROR."""
    results = []
    for n, row in enumerate(rows, start=1):
        kind, payload, took = "error", "never ran", 0.0
        for attempt in range(retries + 1):
            sse, took = runner(row["text"])
            kind, payload = classify_stream(sse)
            if kind != "error":
                break
            if attempt < retries:
                sleep(wait_s)
        if kind == "result":
            top = top_claim(payload)
        elif kind == "no_claim":
            top = {"label": "NO_CLAIM", "band": "", "reasons": ["no checkable claim found"], "routes": []}
        else:
            top = {"label": "ERROR", "band": "", "reasons": [str(payload)], "routes": []}
        results.append({"id": row["id"], "group": row["group"], "truth": row["truth"], "text": row["text"], "lang": row["lang"], "split": row["split"],
                        "label": top["label"], "band": top["band"], "routes": top["routes"], "reasons": top["reasons"], "seconds": round(took, 2),
                        "outcome": "error" if top["label"] == "ERROR" else outcome(row, top["label"]), "built_from": row.get("built_from", ""), "base_id": row.get("base_id", "")})
        print(f"[{n}/{len(rows)}] {row['id']:>6} {row['truth']:<7} -> {top['label']:<17} {top['band']:<6} {results[-1]['outcome']}", flush=True)
    return results


def render(results: list[dict], meta: dict, excluded: list[tuple[str, str]]) -> str:
    ok = [r for r in results if r["outcome"] != "error"]
    s = summarise(ok)
    lines = [f"# Claim sheet: {meta['when']}", "",
             f"Commit `{meta['sha']}`{' (tree had uncommitted changes)' if meta['dirty'] else ''}. Split **{meta['split']}**, fact-check lookup **{meta['factcheck']}**, "
             f"official text **{meta['official']}**. {len(ok)} claims scored, {len(results) - len(ok)} errors, {len(excluded)} excluded as contaminated.", "",
             "Command: `" + meta["command"] + "`", "", "## Headline numbers (each with n and a 95 percent range)", "",
             "| Metric | Result |", "|---|---|",
             f"| **True claims called false** (false-refute rate; the error that does most harm) | {pct(*s['false_refute'])} |",
             f"| **False claims called true** (false-support rate) | {pct(*s['false_support'])} |",
             f"| False claims refuted (recall; changed-number claims are in here) | {pct(*s['refute_recall'])} |",
             f"| Coverage: claims that got a decided label (refuted, supported, missing context) | {pct(*s['coverage'])} |",
             f"| Unknowable claims answered 'cannot tell' or 'not a claim' | {pct(*s['correct_abstention'])} |",
             f"| Median and 95th percentile seconds per claim | {s['seconds_median']} and {s['seconds_p95']} |", "",
             "## By confidence band (correct share among decided answers)", "", "| Band | Correct |", "|---|---|"]
    lines += [f"| {b} | {pct(*v)} |" for b, v in s["precision_by_band"].items()]
    lines += ["", "## By group", "", "| Group | n | Correct | Wrong | Abstained | Decided (coverage) |", "|---|---|---|---|---|---|"]
    names = {"B": "B earlier sheet", "T": "T true, official", "C": "C one number changed", "U": "U unknowable", "R": "R false, no fact-check", "H": "H Hindi drafts", "F": "F false, published"}
    for g, c in s["groups"].items():
        n = c["n"]
        decided = sum(1 for r in ok if r["group"] == g and r["label"] in ("REFUTED", "SUPPORTED", "MISSING_CONTEXT"))
        lines.append(f"| {names.get(g, g)} | {n} | {pct(c.get('correct', 0), n)} | {pct(c.get('wrong', 0), n)} | {c.get('abstain', 0)} | {pct(decided, n)} |")
    routes: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in ok:
        for route in r["routes"] or ["none"]:
            if r["label"] in ("REFUTED", "SUPPORTED", "MISSING_CONTEXT"):
                routes[route][0] += 1
                routes[route][1] += r["outcome"] == "correct"
    lines += ["", "## By evidence route (decided answers only)", "", "| Route | Decided | Correct |", "|---|---|---|"]
    lines += [f"| {k} | {v[0]} | {pct(v[1], v[0])} |" for k, v in sorted(routes.items())] or ["| (none decided) | 0 | n/a |"]

    base = {r["id"]: r for r in ok}
    pairs = [(r, base[r["base_id"]]) for r in ok if r["group"] == "H" and r["base_id"] in base]
    if pairs:
        same = sum(h["label"] == e["label"] for h, e in pairs)
        lines += ["", "## Hindi against English", "", f"Hindi drafts that got the same label as their English original: {pct(same, len(pairs))} ({len(pairs)} pairs with both run)."]

    wrong = [r for r in ok if r["outcome"] == "wrong"]
    lines += ["", f"## Wrong answers ({len(wrong)})", "", "| id | group | claim | said | band | last reasons |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['group']} | {r['text'][:90].replace('|', '/')} | {r['label']} | {r['band']} | {'; '.join(r['reasons'])[:160].replace('|', '/')} |" for r in wrong] or ["| none | | | | | |"]
    if excluded:
        lines += ["", "## Excluded as contaminated (the claim text appears in code, config or unit tests)", ""] + [f"- {rid}: {path}" for rid, path in excluded]
    errs = [r for r in results if r["outcome"] == "error"]
    if errs:
        lines += ["", f"## Errors ({len(errs)})", ""] + [f"- {r['id']}: {'; '.join(r['reasons'])[:140]}" for r in errs]
    return "\n".join(lines) + "\n\n" + LIMITS


# ---- the live runner ----------------------------------------------------------------------------------------------------------

def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def build_runner(factcheck: str, official: str, workdir: Path):
    from fastapi.testclient import TestClient

    from satyasutra.api.app import create_app
    from satyasutra.claims.contrast import OfficialResult, contrast_check
    from satyasutra.config import load_config
    from satyasutra.ledger import SQLiteLedgerStore
    from satyasutra.llm import GeminiClient
    from satyasutra.sources.factcheck import FactCheckResult
    from satyasutra.sources.index import OfficialIndex, Passage

    cfg = load_config()
    kwargs: dict = {}
    if factcheck == "off":
        kwargs["lookup"] = lambda text, lang: FactCheckResult([], 0, False)
    if official == "off":
        kwargs["official"] = lambda text: OfficialResult()
    elif official == "snapshot":
        gemini, index, s = GeminiClient(cfg), OfficialIndex(workdir / "snapshot.sqlite3"), cfg.runtime.official
        for r in read_jsonl(SNAPSHOT):
            when = lambda v: datetime.fromisoformat(v) if v else None
            index.add(Passage(r["source_id"], r["publisher"], r["tier"], r["url"], r["title"], r["text"], r["sha256"], when(r["published_at"]), when(r["fetched_at"])))
        kwargs["official"] = lambda text: contrast_check(gemini, cfg, text, index.search(text, s.max_results, s.min_token_chars))
    from satyasutra import __version__
    app = create_app(cfg, SQLiteLedgerStore(workdir / "ledger.sqlite3", code_version=__version__), **kwargs)
    client = TestClient(app, raise_server_exceptions=False)

    def runner(text: str) -> tuple[str, float]:
        started = time.perf_counter()
        with client.stream("GET", "/agent/stream", params={"text": text}) as r:
            body = "".join(r.iter_text())
        return body, time.perf_counter() - started
    return runner


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--groups", default="", help="comma list, e.g. C,T (default: every group)")
    ap.add_argument("--factcheck", choices=["on", "off"], default="off")
    ap.add_argument("--official", choices=["snapshot", "live", "off"], default="snapshot")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "docs" / "claim-sheet.md"))
    ap.add_argument("--allow-dirty", action="store_true", help="dev runs only")
    a = ap.parse_args(argv)

    rows = read_jsonl(CLAIMS)
    if not rows:
        print("tests/eval/claims.jsonl is empty: run scripts/eval/assemble.py", file=sys.stderr)
        return 2
    dirty = bool(git("status", "--porcelain", "--", "src", "config", "scripts", "tests/eval/claims.jsonl", "tests/eval/authored.yaml"))
    if a.split in ("test", "all") and dirty and not a.allow_dirty:
        print("the test split needs a clean git tree for src, config, scripts and the dataset: commit first (the numbers are tied to a commit)", file=sys.stderr)
        return 2
    leaks = find_leaks(rows, [ROOT / "src", ROOT / "config", ROOT / "tests" / "unit"])
    contaminated = {rid for rid, _ in leaks}
    chosen = [r for r in rows if (a.split == "all" or r["split"] == a.split) and (not a.groups or r["group"] in a.groups.split(","))]
    if a.split in ("test", "all"):
        chosen = [r for r in chosen if r["id"] not in contaminated]
    excluded = sorted({(rid, path) for rid, path in leaks if any(r["id"] == rid for r in rows)})
    if a.limit:
        chosen = chosen[: a.limit]
    sha = git("rev-parse", "--short", "HEAD") or "unknown"
    print(f"{len(chosen)} claims, split {a.split}, factcheck {a.factcheck}, official {a.official}, commit {sha}{' (dirty)' if dirty else ''}", flush=True)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = run_rows(chosen, build_runner(a.factcheck, a.official, Path(tmp)))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    runs = EVAL_DIR / "runs"
    runs.mkdir(exist_ok=True)
    (runs / f"{stamp}-{sha}-{a.split}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8")
    meta = {"when": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "sha": sha, "dirty": dirty, "split": a.split, "factcheck": a.factcheck, "official": a.official,
            "command": "python scripts/eval/run.py " + " ".join(argv if argv is not None else sys.argv[1:])}
    Path(a.out).write_text(render(results, meta, excluded), encoding="utf-8", newline="\n")
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
