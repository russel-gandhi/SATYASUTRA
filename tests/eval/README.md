# Verdict dataset (spec D1)

Measures how often the claim agent is right, how often it refuses to answer, and whether its confidence words mean anything.
Specification: [`docs/SPEC_REPLY_SPREAD_EVAL.md`](../../docs/SPEC_REPLY_SPREAD_EVAL.md). Recorded test data only: nothing here is read by `src/`.

## What is in it (built 31 Oct 2026)

| Group | Rows | Built from | Correct answer |
|---|---|---|---|
| **T** true, official | 16 | One sentence from an RBI or SEBI release we hold (`official_snapshot.jsonl`), figures copied exactly | not `REFUTED` (`SUPPORTED` is a win; cannot-tell is an abstention) |
| **C** one number changed | 13 | Each T row that has a figure: **one number changed by code** (`scripts/eval/lib.py`), recorded in `change` | `REFUTED` (the contrast route's job) |
| **U** unknowable | 25 | Predictions, opinions, fresh local events, vague talk | `UNVERIFIABLE_YET` or `NOT_A_CLAIM`; any decided label is wrong |
| **R** false, no fact-check | 4 | One hoax checked by hand (PM assassination) and three changed names or places, contradicted by the RBI release of 30 Oct | `REFUTED`; cannot-tell is an abstention; `SUPPORTED` is a serious failure. Names and places are **not** covered by the contrast route today, so cannot-tell is the expected result |
| **H** Hindi | 12 | Drafts of T, C, U and B rows | Same as the original. **Written by us, not reviewed by a native speaker** |
| **B** earlier sheet | 21 | The 22-claim `eval/claims.yaml`, minus the assassination row (now R) | as in that sheet |
| **F** false, published | 0 so far | `scripts/eval/build_f.py` needs the Fact Check key and two reviewers | `REFUTED` |

91 rows. A third is `dev` (for tuning), the rest `test` (for one final run per frozen version); the split is by hash of the row's root id, so a
claim, its changed-number twin and its Hindi version always sit on the same side.

## Commands

```
python scripts/eval/build_official.py          # save today's RBI and SEBI items; run again on later days: it only adds (more T and C rows)
python scripts/eval/assemble.py                # rebuild claims.jsonl from authored.yaml + snapshot + f_rows.jsonl (never edit claims.jsonl by hand)
python scripts/eval/build_f.py                 # needs GOOGLE_FACTCHECK_API_KEY: candidate false claims, all pending review
python scripts/eval/run.py --split dev --factcheck off --official snapshot --limit 10     # quick look
python scripts/eval/run.py --split dev --factcheck off --official snapshot                # the dev table  -> docs/claim-sheet.md
python scripts/eval/run.py --split dev --factcheck on  --official snapshot --out docs/claim-sheet-factcheck-on.md
python scripts/eval/run.py --split test ...    # once per frozen version; refuses on a dirty tree; skips contaminated rows
pytest tests/unit/test_eval_dataset.py tests/unit/test_eval_run.py
```
`run.py` needs `GEMINI_API_KEY` and, with `--factcheck on`, `GOOGLE_FACTCHECK_API_KEY` in `.env`. At the free tier (15 requests a minute, several calls per claim) the
91 rows take a long while: use `--limit` and `--groups C,T` while developing. Every prediction is written to `tests/eval/runs/`.

## Rules that keep it honest

1. `--factcheck off` for the false groups, or the system just finds the review the claim came from. Run the same rows again with it on for "coverage when a fact-checker exists".
2. Tune thresholds on `dev` only. Run `test` once per frozen version and never adjust anything from it.
3. A `test` claim that appears in `src/`, `config/` or `tests/unit/` is **contaminated**: the harness leaves it out and lists it. Today that is `b-t01` ("Smoking causes lung cancer"):
   the code's own comments and thresholds use it as an example.
4. T and C labels come from the release text and from code, not from opinion. U, R and B have one labeller and say so in `review_status`; get a second, independent look.
5. Re-running `build_official.py` later keeps old releases (the feeds only hold the newest), so claims built from them stay checkable.

## Known limits

- **Finance only.** Official text we can reach is the RBI and SEBI feeds (PIB, MoHFW, cybercrime.gov.in answer 403). T and C describe finance claims, not general accuracy.
- Only 10 RBI releases are in today's snapshot, which caps T and C. Run `build_official.py` daily for more.
- `c11` changes 95 to 96: a one-point change is a deliberately subtle case.
- The system has no route that can return `SUPPORTED` from official text (a matching number never gives it), so expect near zero `SUPPORTED` on T; the number that matters there is how often a true claim is called false.
- 120 to 140 rows cannot show rare failures; ranges in the claim sheet show how wide the doubt is.

## Finding to act on: the Telegram bot skips the official-text check

The bot calls `POST /verify` (`src/satyasutra/bot/telegram.py`). `/verify` runs `pipeline.verify_post`, which has no official-text step. Only `GET /agent/stream`
(the web page) runs the contrast check against RBI and SEBI text. So a forwarded changed-number claim reaches the bot's user as "cannot tell" even though the same text
refutes on the web page. Fix (integrator files `api/app.py` and `pipeline.py`): pass `do_official` into the `/verify` path, or have the bot call the agent route.
This harness uses the agent route on purpose, so its numbers describe the web page; the bot cannot match them until that is fixed.
