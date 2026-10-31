# Plan to the deadline (2 Nov 2026, 00:00)

> **Superseded (31 Oct, 13:20 IST)** by [`BUILD_PLAN.md`](BUILD_PLAN.md) (schedule, checkpoints, cut order) and [`BUILD_MAP_FINAL.md`](BUILD_MAP_FINAL.md) (packages, owners, file ownership). Kept for history.

Written 31 Oct 2026, about 33 hours before the deadline. Goal: a working prototype that gives real results and does not break.
Not a benchmark exercise. The no-mock-data rule and the "say what was not tested" rule still apply.

## Where we are

Working on branch `phase-8/prototype-ui` (pushed):

- Claim agent (LangGraph): extract, Fact Check registry and Wikidata in parallel, same-claim judge, rules, ledger. Hindi works through an English rendering.
- Live fast clock on the Bluesky stream, clusters ranked by distinct authors per minute, optional escalation.
- Media: debunked-image index (117 items), TruFor edit detection on the GPU, 8-frame video check, heatmaps.
- One-page UI, hash-chained ledger with an integrity check. 338 unit tests pass.

Not done: AI-generated-image detector, audio detector, origin and spread view, Telegram bot, reliability pass, README, evaluation.

## Decisions waiting on the user

1. How to run the Smaug detector. Option A: my own short wrapper that loads the weights through `SentenceTransformer` without running the repo's `.py`. Option B: the user allows running the repo script. Review of the repo code found no network calls, no `exec`, no `trust_remote_code`.
2. Whether to add an audio detector (needs licence check and download approval; ships as experimental and uncalibrated).
3. A fresh Telegram token in `.env`, entered by the user, if the bot is wanted.

## Work blocks, in order

Each block ends in something that runs. Hours are estimates, not measurements.

### Block 1 - Smaug AI-image detector (2-3 h)
- Run it on Wikimedia Commons "AI-generated images" (positives) and ordinary Commons photos (negatives). Record hits, false alarms and the score spread in `docs/calibration/`.
- Add it as a second media signal in `media.py`, `/media/check` and the UI: "chance this is AI-generated" next to the TruFor edit score. Put the cut in `thresholds.yaml` only from the measured data.
- Result text states the limits: new generators can slip through, a real photo with a small AI edit can read as real, the model's published accuracy is the authors' own figure.
- Done when: unit tests plus a live run on Commons images pass and the numbers are written down.

### Block 2 - Origin and spread view (4-5 h)
This is the "blast radius / backtracking" feature. Bluesky only, and the UI says so.
- Persist cluster members (post URI, author hash, time) in SQLite so spread survives past the 300 s window.
- Timeline for a cluster: first post, authors in time order, count over time.
- Variant view: posts in a cluster ordered by first-seen time with a word-level diff between consecutive variants, original quote kept.
- Link a checked claim to a live cluster when its text matches one.
- UI: a "trace" panel with the timeline and a spread chart.
- Done when: a live cluster shows a first post, a variant diff and a spread count in the UI.

### Block 3 - Reliability pass (3-4 h)
- `scripts/preflight.py`: checks key names are set (never values), Gemini, Fact Check, Wikidata, TruFor service, ffmpeg, the index. Prints a table.
- `scripts/start_demo.ps1`: starts the API and the TruFor service in order.
- Gemini 15 RPM friendliness: a waiting message in the UI and one retry with backoff after a timeout.
- Graceful-failure tests for Fact Check and Wikidata outages (result says "source unreachable", never a guess).
- Screenshots of a good run kept as a backup (the live demo is still live).
- Done when: preflight is green, a full demo run works from a cold start, the test suite and the guard pass.

### Block 4 - Small measured evaluation and README (3-4 h)
- Run claims taken from fact-check feeds through the agent. Report REFUTED vs cannot-tell on a hand-checked sample, with counts.
- README: what is real, what is roadmap (table), what each model does and does not catch, licences and credit (TruFor by GRIP-UNINA, Smaug by Abacus.AI), demo script.
- Done when: README and the evaluation numbers are committed.

### Block 5 - The rest of the original phases, using pretrained models (kept in scope, run in this order)
Nothing from the original 10 phases is dropped. Each item below uses an existing model or plain code, ships with its limits stated,
and is cut from the schedule only if Block 6 is at risk, never from the plan.
- **5a Phase 6 audio forensics (4-6 h).** A pretrained anti-spoofing model (W2V2-AASIST class) plus the plan's gate (duration, format, codec). Experimental, uncalibrated; tested on real speech vs TTS clips we generate, with that weakness stated. Needs licence check and download approval.
- **5b Phase 2 F2-01 and F2-02 (3-5 h each).** Images in posts to fingerprints, media clusters ranked like text clusters. Plain code on the existing PDQ fingerprints.
- **5c Phase 8 Telegram bot (2-3 h).** Forwarded text or image goes through the same agent and media check, reply with the verdict card. Needs a fresh token in `.env` from the user.
- **5d Phase 7 face-swap / synthetic video (4-6 h).** A pretrained deepfake video detector run on the sampled frames (candidates: GenConViT, a DFD-style classifier; weights and licence to be checked, FF++/Celeb-DF-trained models are often non-commercial, which is acceptable for a hackathon but must be credited). Experimental; tested on a few real clips vs known deepfake clips, with the small sample stated.
- **5e Phase 3 MiniCheck (2-3 h).** Install from its GitHub branch (needs user approval), use a smaller sibling model that fits the 8 GB GPU, as a second opinion on whether retrieved text supports a claim. Evidence role stays `context` unless it is Tier 1 or 2.
- **5f Phase 5 more languages (2 h).** Run the existing Gemini translate path on more Hindi claims and one or two other Indian languages; report how many work.
- **5g Phase 4 reverse search.** The paid API stays out. Replace it with the debunked-image index, the Block 2 earlier-appearance view, and one-click Lens / Yandex / TinEye search links in the result card (the user presses them; we do not scrape or solve CAPTCHAs).

### Block 6 - Wrap-up (last 3 h before the deadline, nothing new after this)
- Rehearse the demo twice, full run, from a cold start.
- Merge the working branches into `main` and log the partial phases in `PHASES.md` with what was not verified.
- Check CI on the latest push. Re-issue or remove the exposed Telegram token before any public demo.

## Risks of keeping the full scope
- Face-swap video (5d) is the hardest to validate: public datasets are small and pretrained detectors often fail on new clips. It ships labelled experimental, and the result must never say "real".
- Audio (5a) can only be tested on TTS we generate, which is weak evidence of real-world accuracy.
- Reverse image search (5g) cannot be done with a model: Cloud Vision is paid, so it stays an index plus manual links.
- Reach outside Bluesky is not possible: we only see Bluesky.
- Hours: Blocks 1-4 are 12-16 h and Block 5 adds about 20-30 h, more than the ~33 h left with sleep. So Block 5 is a priority queue (5a, 5d, 5b, 5c, 5e, 5f, 5g by demo value), and whatever is unfinished at the Block 6 cutoff stays in the README as roadmap, labelled as not built.

## Standing caveats (repeat these in the demo)
- A SUPPORTED verdict cannot appear live: no positive rating is mapped.
- Escalation has never fired on a real item.
- Thresholds were calibrated on chatter and Commons photos only.
- TruFor misses most edits after video compression.
- Hindi quality beyond a few claims is unproven.
- A detector finding nothing is INCONCLUSIVE, never "authentic".
- Spread numbers describe Bluesky only.

## Time budget
Blocks 1-4 are about 12-16 h of work, leaving slack for waiting on Gemini limits, debugging and sleep. Block 5 items are taken one at a time in the order above; anything unfinished at the Block 6 cutoff is listed as roadmap, not hidden.
