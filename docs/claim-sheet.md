# Claim sheet: 2026-10-31 14:42 UTC

Commit `5174e86`. Split **dev**, fact-check lookup **off**, official text **snapshot**. 32 claims scored, 0 errors, 2 excluded as contaminated.

Command: `python scripts/eval/run.py --split dev --factcheck off --official snapshot --out docs/claim-sheet.md`

## Headline numbers (each with n and a 95 percent range)

| Metric | Result |
|---|---|
| **True claims called false** (false-refute rate; the error that does most harm) | 0/12 (0%, 0 to 24%) |
| **False claims called true** (false-support rate) | 0/12 (0%, 0 to 24%) |
| False claims refuted (recall; changed-number claims are in here) | 7/12 (58%, 32 to 81%) |
| Coverage: claims that got a decided label (refuted, supported, missing context) | 7/32 (22%, 11 to 39%) |
| Unknowable claims answered 'cannot tell' or 'not a claim' | 8/8 (100%, 68 to 100%) |
| Median and 95th percentile seconds per claim | 12.1 and 35.71 |

## By confidence band (correct share among decided answers)

| Band | Correct |
|---|---|
| high | 3/3 (100%, 44 to 100%) |
| medium | 4/4 (100%, 51 to 100%) |
| low | n/a |

## By group

| Group | n | Correct | Wrong | Abstained | Decided (coverage) |
|---|---|---|---|---|---|
| B earlier sheet | 6 | 3/6 (50%, 19 to 81%) | 0/6 (0%, 0 to 39%) | 3 | 2/6 (33%, 10 to 70%) |
| C one number changed | 6 | 3/6 (50%, 19 to 81%) | 0/6 (0%, 0 to 39%) | 3 | 3/6 (50%, 19 to 81%) |
| H Hindi drafts | 6 | 2/6 (33%, 10 to 70%) | 0/6 (0%, 0 to 39%) | 4 | 2/6 (33%, 10 to 70%) |
| T true, official | 7 | 0/7 (0%, 0 to 35%) | 0/7 (0%, 0 to 35%) | 7 | 0/7 (0%, 0 to 35%) |
| U unknowable | 7 | 7/7 (100%, 65 to 100%) | 0/7 (0%, 0 to 35%) | 0 | 0/7 (0%, 0 to 35%) |

## By evidence route (decided answers only)

| Route | Decided | Correct |
|---|---|---|
| contrast | 4 | 4/4 (100%, 51 to 100%) |
| web | 3 | 3/3 (100%, 44 to 100%) |

## Hindi against English

Hindi drafts that got the same label as their English original: 5/6 (83%, 44 to 97%) (6 pairs with both run).

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
