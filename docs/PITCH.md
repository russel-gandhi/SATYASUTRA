# Pitch outline (package G1)

Built on **Radar, Verifier, Responder**, not on a detector. Every number below is copied from a file in the repository and comes with the command that made it.
If a number is not here, it is not on a slide. Rerun the command on the day and update before presenting.

## 1. The problem (20 s)
Rumours in Indian languages move faster than fact-checks. People need an answer that says what is known, from whom, and says "cannot tell" when it is not known.

## 2. Radar: what is spreading now
- 22 live feeds plus Google Trends India, read and grouped with no model calls. Full refresh median 4.5 s; matching a pasted claim about 10 ms.
  Source: `docs/throughput.md`, `python scripts/bench_pulse.py --runs 3` (31 Oct 2026, this machine, one network).
- Says plainly: a window on Indian media and search attention, not WhatsApp.

## 3. Verifier: it checks, and it can refuse
- Claim agent: extract, fact-check registry, official RBI/SEBI text, web search, Wikidata, same-claim judge, plain-code rules. Models only point; code decides.
- Dev claim sheet, 32 claims, lookup off (`python scripts/eval/run.py --split dev --factcheck off --official snapshot`, `docs/claim-sheet.md`):
  - wrong answers 0 of 32; true claims called false 0/12 (range 0 to 24%); false claims called true 0/12 (0 to 24%)
  - unknowable claims answered "cannot tell" 8/8
  - false claims refuted 7/12 (32 to 81%); coverage 7/32 (11 to 39%). **Coverage is low by design: finance-only official text, and "cannot tell" is a result.**
  - The first run had 4 true claims wrongly refuted; we found the cause (a neighbouring table cell), fixed it and reran. Tell this story: it shows the harness works.
- Lookup on (`--factcheck on`, `docs/claim-sheet-factcheck-on.md`): 29 of 32 claims scored (3 errors, not counted), wrong 0, false claims refuted 8/12 (39 to 86%), coverage 8/29 (15 to 46%). The 3 errored rows are not a result; rerun before quoting.
- Test split (run once, 2026-11-01, tag `eval-freeze-2026-11-01`, lookup off, 62 rows, 0 errors): true claims called false 0/15; false claims called true **1/24** (row r06, an old event worded as new); false claims refuted 12/24; coverage 14/62; unknowable answered 'cannot tell' 23/23. **The web-search rule failed its own pre-written criteria** (`docs/WEB_RULE_CRITERIA.md`, `docs/claim-sheet-test.md`). A recency check was added afterwards; it is unit-tested but not measured on held-out data. Say all of this out loud.
- Media: AI-made images 36/40 caught, 1/40 flagged (`docs/calibration/smaug_2026-10-31.txt`); edits flagged at TruFor 0.7 (30 photos, `docs/calibration/trufor_2026-10-31.txt`);
  synthetic speech 19/19 caught, 0/20 real flagged on a weak set (`docs/calibration/audio_synthetic_2026-10-31.txt`). Never "authentic", only "no sign found".

## 4. Responder: what a person gets
Plain-language verdict with links, a Hindi/English Trust Report, correction card, next steps (1930, cybercrime.gov.in, Chakshu; secondary sources, not confirmed on official sites), guidance-only help for leaked private images.
Telegram bot with the same checks; messages from the bot are not stored.

## 5. Responsible AI (30 s)
Cannot tell is a valid answer; sensitive topics (religion, caste, elections) never decided by the system alone (A4); nothing is ever filed; hash-chained ledger of every verdict;
stored vs not stored is written in the README; PIB and some ministries refuse our honest client and we do not disguise it.

## 6. Feasibility
Free-tier model limit 15 requests a minute per model (`docs/gemini_limits.md`), so the fast path uses no model; GPU service on one RTX 4060 for TruFor, Smaug, Whisper, voice and NLI. Cost per verdict and the load test are **not measured yet** (package B4).

## 7. Not built, said out loud
WhatsApp intake, face-swap and AI-video detection, cloned-voice detection, counselling and takedown, legal advice, Scam Guard (live call warning). Hindi/Kannada wording unreviewed by a native speaker.

## Questions to be ready for
- "Why so few decided answers?" Because we only decide with Tier 1/2 evidence; the sheet shows the price (coverage) and the benefit (0 wrong of 32).
- "How do you know it is not tuned to your data?" Dev for tuning, test run once on a tagged commit; one fix was made after dev and is described above.
- "Is the bot the same as the web?" Yes since `POST /verify` runs the official-text check (tested with fakes; the live bot path is not yet rehearsed).
