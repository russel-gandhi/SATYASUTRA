# Claim sheet: 2026-10-31 19:57 UTC

Commit `a5fe547` (tree had uncommitted changes). Split **dev**, fact-check lookup **on**, official text **snapshot**. 7 claims scored, 0 errors, 2 excluded as contaminated.

Command: `python scripts/eval/run.py --split dev --groups U --factcheck on --official snapshot --out docs/claim-sheet-u-factcheck-on.md`

## Headline numbers (each with n and a 95 percent range)

| Metric | Result |
|---|---|
| **True claims called false** (false-refute rate; the error that does most harm) | n/a |
| **False claims called true** (false-support rate) | n/a |
| False claims refuted (recall; changed-number claims are in here) | n/a |
| Coverage: claims that got a decided label (refuted, supported, missing context) | 0/7 (0%, 0 to 35%) |
| Unknowable claims answered 'cannot tell' or 'not a claim' | 7/7 (100%, 65 to 100%) |
| Median and 95th percentile seconds per claim | 20.43 and 54.82 |

## By confidence band (correct share among decided answers)

| Band | Correct |
|---|---|
| high | n/a |
| medium | n/a |
| low | n/a |

## By group

| Group | n | Correct | Wrong | Abstained | Decided (coverage) |
|---|---|---|---|---|---|
| U unknowable | 7 | 7/7 (100%, 65 to 100%) | 0/7 (0%, 0 to 35%) | 0 | 0/7 (0%, 0 to 35%) |

## By evidence route (decided answers only)

| Route | Decided | Correct |
|---|---|---|
| (none decided) | 0 | n/a |

## Wrong answers (0)

| id | group | claim | said | band | last reasons |
|---|---|---|---|---|---|
| none | | | | | |

## Excluded as contaminated (the claim text appears in code, config or unit tests)

- b-t01: config\thresholds.yaml
- b-t01: src\satyasutra\claims\rules.py

## Limits of these numbers

- The true and changed-number groups are built from official releases we can reach: today the RBI and SEBI feeds (PIB, MoHFW and cybercrime.gov.in
  answer 403). They describe **finance claims**, not general accuracy.
- Claims come from the team, one labeller for most groups. The labels of the T and C groups come from the release text and from the code that changed
  the number; U, R and the earlier sheet (B) still need an independent second look. Hindi rows are our own drafts, not reviewed by a native speaker.
- The set is small. Differences of a few claims are noise; the ranges show how wide the doubt is. It cannot show rare failures.
- Numbers describe one version of the code on one day. Fact-checks and news change, so a rerun on another day can differ.

## Note added 2026-11-01

The first run of this sheet (commit `a5fe547`) called u24, "The monsoon will be weaker than normal next year.", SUPPORTED at medium: outlets reporting a forecast were counted as confirmation.
A `forecast_terms` hold was added (`config/runtime.yaml` `web`, `claims/web.py`, one unit test) and the 7 unknowable dev rows were rerun: u24 is now cannot-tell, 0 wrong of 7.
This was tuned on a dev row, so it is not held-out evidence. The word list was read from this one row and will miss other forecast wordings. The held-out test split is unchanged.
