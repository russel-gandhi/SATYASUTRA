"""Run the labelled questions in docs/raksha-rag-questions.yaml through the grounded guide, live (needs the built index and a Gemini key).

    python scripts/raksha_rag_eval.py [--out docs/calibration/raksha_rag_YYYY-MM-DD.txt]

Reports: retrieval hit (an expected document is among the retrieved passages), grounded and correct answers for answerable questions, correct refusals
for unanswerable ones, and every miss. One labeller, one set, run once: a first measurement, not a held-out evaluation.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.errors import SourceError  # noqa: E402
from satyasutra.llm import GeminiClient  # noqa: E402
from satyasutra.raksha.ask import DEVANAGARI, answer_question, retrieve, to_english  # noqa: E402
from satyasutra.raksha.corpus import CorpusIndex, load_rag  # noqa: E402


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    load_env_file(ROOT / ".env")
    rag, client = load_rag(), GeminiClient(load_config())
    index = CorpusIndex(rag)
    rows = yaml.safe_load((ROOT / "docs" / "raksha-rag-questions.yaml").read_text(encoding="utf-8"))
    lines, misses = [], []
    n_ans = n_hit = n_grounded = n_correct = n_unans = n_refused = errors = 0
    for item in rows:
        q = item["q"]
        lang = "hi" if DEVANAGARI.search(q) else "en"
        try:
            ans = answer_question(client, index, rag, q, lang)
        except SourceError as exc:
            errors += 1
            lines.append(f"ERROR  {q}  ({type(exc).__name__})")
            continue
        got_docs = {c.doc_id for c in retrieve(index, rag, ans.english_query or q)}
        text = " ".join(s["text"] for s in ans.sentences).casefold()
        if item["answerable"]:
            n_ans += 1
            hit = bool(got_docs & set(item["docs"]))
            grounded = not ans.refused
            correct = grounded and (not item["contains"] or any(w.casefold() in text for w in item["contains"]))
            n_hit, n_grounded, n_correct = n_hit + hit, n_grounded + grounded, n_correct + correct
            lines.append(f"{'ok ' if correct else 'MISS'}  retrieved={'Y' if hit else 'N'} grounded={'Y' if grounded else 'N'}  {q}")
            if not correct:
                misses.append(q)
        else:
            n_unans += 1
            refused = ans.refused
            n_refused += refused
            lines.append(f"{'ok ' if refused else 'MISS'}  refused={'Y' if refused else 'N'} (retrieved {ans.retrieved})  {q}")
            if not refused:
                misses.append(q)
        time.sleep(4)                                   # stay under the free tier's per-minute limit
    report = [f"Raksha grounded guide, {time.strftime('%Y-%m-%d')}: {len(rows)} questions ({n_ans} answerable, {n_unans} not), {errors} errors",
              f"retrieval: an expected document in the retrieved passages for {n_hit}/{n_ans}",
              f"grounded (at least one quoted sentence): {n_grounded}/{n_ans}",
              f"correct (a sentence holds an expected word): {n_correct}/{n_ans}",
              f"refused when no document answers: {n_refused}/{n_unans}",
              "quotes verbatim: every sentence shown passed the verbatim and figure checks (enforced in code, so this is not a measured rate)",
              "", *lines, "", "misses:", *(f"- {m}" for m in misses)]
    out = "\n".join(report)
    print(out)
    if args.out:
        args.out.write_text(out + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
