# Where the dev answers fell short (package B1), 2026-10-31

Source: `tests/eval/runs/20261031-1442-5174e86-dev.jsonl` (lookup off, official snapshot). Commit `5174e86`. 32 dev claims, 0 wrong, 17 abstained.
Buckets were assigned by reading the reasons line of each row, not by a second reader.

| Bucket | Rows | What happened |
|---|---|---|
| Genuinely unknowable by design: a true official claim cannot be SUPPORTED | t04, t08, t09, t10, t12, t13, t14, h01, h03, h04 (10) | No route turns a matching number into SUPPORTED, so true official claims end in "cannot tell". This is the safe choice and the main reason coverage is 22%. |
| Contrast did not fire on a changed number | c08, c12, c13, hc03 (4) | The changed number was not refuted. c08 and hc03 also abstained in the first run; c12 refuted in the first run and not after the table-cell fix, which may be model variance or the new rule: **not investigated**. |
| No fact-check, lookup off | b-f09, b-t06, b-t08 (3) | Health claims with no official text in our index; with the lookup on, 3 of these rows reached decided answers (see the lookup-on sheet). |

Not covered by any row: extraction failures, translation errors, same-claim judge errors and retrieval misses were not separately observed in this set (0 wrong answers).
The first dev run had 4 wrong answers (true claims refuted by a neighbouring table cell); fixed in `claims/contrast.py` and rerun: see `docs/claim-sheet.md`.
