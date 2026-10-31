"""Run the evaluation claim sheet (eval/claims.yaml) through the live API's /verify and write a plain report.

Usage: python scripts/eval_claims.py --base http://127.0.0.1:8000 --out docs/evaluation_<date>.md
What it measures, in the order that matters:
  wrongly refuted  a TRUE claim that came back REFUTED (the error that does the most harm)
  wrongly supported a FALSE claim that came back SUPPORTED
  refuted          a FALSE claim that came back REFUTED (recall of the system on rumours that fact-checkers have covered)
  cannot tell      anything that came back UNVERIFIABLE_YET or with no claim extracted (a valid answer, but not a solved one)
It is a small hand-made sheet, not a benchmark, and the report says so.
"""
import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def top_label(body: dict) -> tuple[str, str, int | None]:
    claims = body.get("claims") or []
    if not claims:
        return "NO_CLAIM", "", None
    order = ["REFUTED", "MISSING_CONTEXT", "SUPPORTED", "UNVERIFIABLE_YET", "NOT_A_CLAIM"]
    best = min(claims, key=lambda c: order.index(c["label"]) if c["label"] in order else len(order))
    return best["label"], best["confidence_band"], best.get("ledger_seq")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--out", required=True)
    ap.add_argument("--sheet", default=str(ROOT / "eval" / "claims.yaml"))
    a = ap.parse_args()
    sheet = yaml.safe_load(Path(a.sheet).read_text(encoding="utf-8"))["claims"]
    for c in sheet:
        c["truth"] = str(c["truth"]).lower()                       # YAML reads an unquoted true or false as a boolean
    rows = []
    with httpx.Client(base_url=a.base, timeout=180) as http:
        for c in sheet:
            for attempt in range(3):
                r = http.post("/verify", json={"text": c["text"]})
                if r.status_code != 503:
                    break
                time.sleep(30)                                   # the model said it was at its limit: wait and ask again, once per half minute
            if r.status_code != 200:
                rows.append({**c, "label": f"ERROR {r.status_code}", "band": "", "seq": None, "reasons": []})
                print(c["id"], "ERROR", r.status_code, flush=True)
                continue
            body = r.json()
            label, band, seq = top_label(body)
            reasons = [x for cl in body.get("claims", []) for x in cl.get("reasons", [])][-2:]
            rows.append({**c, "label": label, "band": band, "seq": seq, "reasons": reasons})
            print(c["id"], c["truth"], "->", label, band, flush=True)
    err = [r for r in rows if r["label"].startswith("ERROR")]
    scored = [r for r in rows if r not in err]
    by = lambda truth: [r for r in scored if r["truth"] == truth]
    f, t, u = by("false"), by("true"), by("unknown")
    n = lambda xs, *labels: sum(x["label"] in labels for x in xs)
    lines = [f"# Evaluation on the claim sheet, {datetime.now(timezone.utc).date()}", "",
             "A small hand-made sheet (`eval/claims.yaml`), **not an independent benchmark**: the truth labels are widely documented facts and widely debunked rumours chosen by us.",
             "Each claim was sent to `POST /verify` on the running prototype, with live Gemini, Fact Check and the other sources of that moment. One run each; results can differ on another day or after a fact-check is published.", "",
             "## Summary", "",
             f"- Claims run: {len(scored)} of {len(rows)} ({len(err)} errors)",
             f"- **True claims wrongly refuted: {n(t, 'REFUTED')} of {len(t)}**",
             f"- **False claims wrongly supported: {n(f, 'SUPPORTED')} of {len(f)}**",
             f"- False claims refuted: {n(f, 'REFUTED')} of {len(f)}; marked misleading: {n(f, 'MISSING_CONTEXT')}; cannot tell: {n(f, 'UNVERIFIABLE_YET', 'NO_CLAIM', 'NOT_A_CLAIM')}",
             f"- True claims supported: {n(t, 'SUPPORTED')} of {len(t)}; cannot tell: {n(t, 'UNVERIFIABLE_YET', 'NO_CLAIM', 'NOT_A_CLAIM')}",
             f"- Claims nobody can have checked: cannot tell {n(u, 'UNVERIFIABLE_YET', 'NO_CLAIM', 'NOT_A_CLAIM')} of {len(u)}; any decided label here ({n(u, 'REFUTED', 'SUPPORTED', 'MISSING_CONTEXT')}) needs a look",
             "", "Reading it: the system only decides when a trusted source has checked the very same claim, so a high 'cannot tell' rate on true everyday facts is expected, not a failure. "
             "A decided label on the wrong side is the failure that counts.", "",
             "## Every claim", "", "| id | truth | claim | result | confidence | ledger | last reasons |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['truth']} | {r['text']} | {r['label']} | {r['band']} | {r['seq'] or ''} | {'; '.join(r['reasons']).replace('|', '/')} |")
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
