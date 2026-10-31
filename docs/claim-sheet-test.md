# Claim sheet: 2026-10-31 19:49 UTC

Commit `0d8ef61`. Split **test**, fact-check lookup **off**, official text **snapshot**. 62 claims scored, 0 errors, 2 excluded as contaminated.

Command: `python scripts/eval/run.py --split test --factcheck off --official snapshot --out docs/claim-sheet-test.md`

## Headline numbers (each with n and a 95 percent range)

| Metric | Result |
|---|---|
| **True claims called false** (false-refute rate; the error that does most harm) | 0/15 (0%, 0 to 20%) |
| **False claims called true** (false-support rate) | 1/24 (4%, 1 to 20%) |
| False claims refuted (recall; changed-number claims are in here) | 12/24 (50%, 31 to 69%) |
| Coverage: claims that got a decided label (refuted, supported, missing context) | 14/62 (23%, 14 to 34%) |
| Unknowable claims answered 'cannot tell' or 'not a claim' | 23/23 (100%, 86 to 100%) |
| Median and 95th percentile seconds per claim | 11.69 and 24.19 |

## By confidence band (correct share among decided answers)

| Band | Correct |
|---|---|
| high | 7/7 (100%, 65 to 100%) |
| medium | 6/7 (86%, 49 to 97%) |
| low | n/a |

## By group

| Group | n | Correct | Wrong | Abstained | Decided (coverage) |
|---|---|---|---|---|---|
| B earlier sheet | 14 | 7/14 (50%, 27 to 73%) | 0/14 (0%, 0 to 22%) | 7 | 5/14 (36%, 16 to 61%) |
| C one number changed | 7 | 5/7 (71%, 36 to 92%) | 0/7 (0%, 0 to 35%) | 2 | 5/7 (71%, 36 to 92%) |
| H Hindi drafts | 6 | 4/6 (67%, 30 to 90%) | 0/6 (0%, 0 to 39%) | 2 | 1/6 (17%, 3 to 56%) |
| R false, no fact-check | 8 | 1/8 (12%, 2 to 47%) | 1/8 (12%, 2 to 47%) | 6 | 2/8 (25%, 7 to 59%) |
| T true, official | 9 | 1/9 (11%, 2 to 43%) | 0/9 (0%, 0 to 30%) | 8 | 1/9 (11%, 2 to 43%) |
| U unknowable | 18 | 18/18 (100%, 82 to 100%) | 0/18 (0%, 0 to 18%) | 0 | 0/18 (0%, 0 to 18%) |

## By evidence route (decided answers only)

| Route | Decided | Correct |
|---|---|---|
| contrast | 5 | 5/5 (100%, 57 to 100%) |
| web | 9 | 8/9 (89%, 57 to 98%) |

## Hindi against English

Hindi drafts that got the same label as their English original: 6/6 (100%, 61 to 100%) (6 pairs with both run).

## Wrong answers (1)

| id | group | claim | said | band | last reasons |
|---|---|---|---|---|---|
| r06 | R | The Reserve Bank of India has announced tonight that 500 and 1000 rupee notes will stop be | SUPPORTED | medium | BBC: shown as context only (tier 2 or not enough outlets agree); Newslaundry: shown as context only (tier 3 or not enough outlets agree); several reputable outl |

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

## A5 decision: the web-search rule FAILED its pass criteria (written 2026-11-01 after the run, criteria in `docs/WEB_RULE_CRITERIA.md`)

Run once on tag `eval-freeze-2026-11-01` (code commit `9f91270`; the sheet header shows `0d8ef61`, which differs only in `ui/`). 62 rows scored, 0 errors.

| Criterion | Result | Verdict |
|---|---|---|
| 1. No false claim called SUPPORTED, and false-support rate at most 1 in 30 | 1/24 (4%, 1 to 20%): row r06 | **Fail** |
| 2. No true claim REFUTED | 0/15 | Pass |
| 3. r05 to r08 never SUPPORTED | r05 cannot tell, r07 refuted, r08 cannot tell, **r06 SUPPORTED (medium)** | **Fail** |

r06 states the 2016 demonetisation as if announced tonight. Reputable outlets reported that event as fact, so two outlets agreed, and nothing in the rule asks whether the coverage is
old news. This is exactly the failure the row was written to catch. The rule as frozen does not handle an old event worded as new.

What happens next, as pre-agreed: the test split is spent for this version and these numbers stay as they are. The tightening (a recency check on SUPPORTED) is developed and
checked on dev rows and unit tests only; its effect on the held-out rows cannot be measured, because the R rows exist only in the test split. Any later claim about it is "fixes the
known failure mode", not "measured held-out accuracy".

**Tightening made after the run (2026-11-01, commit follows this sheet):** web coverage cannot support a claim that is worded as happening now (`report.recent_terms`: "tonight", "today", ...)
when the newest agreeing headline from a reputable outlet is older than `report.stale_days` (14) or undated; it is shown as context and the reason line says why (`claims/web.py`, 3 unit
tests with real-shaped hits). **Not verified live and not measured:** r06 was not rerun, because test rows are not used to check a fix; the held-out failure stays on the record. It only catches
claims that use a "now" word, so an old event stated without one still passes the rule.
