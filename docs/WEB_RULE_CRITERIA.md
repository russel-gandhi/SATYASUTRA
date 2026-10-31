# Pass criteria for the web-search rule (package A5), written 2026-10-31 BEFORE the test split is run

The web rule lets a claim be called SUPPORTED when two distinct reputable outlets report it as fact (confidence capped at medium), and lets a debunk count from
one fact-checker or two reputable outlets.

The rule **passes** if, on the test split run once on a tagged commit:
1. no false claim (groups C, R, F, and any U row) is called SUPPORTED, and the false-support rate is at most 1 in 30 (the range is shown, with the small n stated);
2. no true claim (group T, official) is called REFUTED;
3. rows built to fool it (r05 and r06 old events stated as new, r07 and r08 rumours that outlets report only to debunk) each end as REFUTED or cannot-tell, never SUPPORTED.

If it **fails**: tighten (more outlets, a recency check, treat a headline containing a debunk word as a debunk) and rerun on dev rows only; the test split stays spent for this version, and the failure is written in the claim sheet as it is.
Test rows are never used to tune. The R rows all fell in the test split by hash, so they have never been run on dev: their first run is the final one.
