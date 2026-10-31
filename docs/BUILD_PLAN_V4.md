# Build plan (v4): the order of work

What to build and in what order. The packages, their files and their "done when" are in [`BUILD_MAP_V4.md`](BUILD_MAP_V4.md); the detailed design of Raksha and Scam Guard is in
[`PLAN_RAKSHA_SCAM_GUARD.md`](PLAN_RAKSHA_SCAM_GUARD.md); how the dataset and harness work is in `tests/eval/README.md` and [`SPEC_REPLY_SPREAD_EVAL.md`](SPEC_REPLY_SPREAD_EVAL.md).

## How to read this plan

- Work is ordered by **dependency and risk**. A stage is finished when its **exit conditions** pass. Independent work from a later stage may start earlier, but nothing in a stage is shown
  to anyone until that stage's exit conditions hold.
- Every package has a tier. **M** must land. **S** should land. **C** could land. If capacity is short, drop C first, then S, and never M.
- A package is done only when: the code or content exists, its tests pass, wording lives in `config/`, the claim sheet or README row that mentions it is true, and its "done when" has been
  seen to pass (not assumed).
- Four lanes can run side by side once Stage 0 is finished: **Integrator** (shared files and live runs), **Claims and measurement**, **Citizen help**, and **People** (reviews, confirmations,
  choosing real items). Shared files are edited only by the integrator; everyone else sends a patch or works in new modules.

## Stage 0: one branch of truth

Packages: F1, F2, F3 (first pass), F4.

1. Merge the CI fix (installs ffmpeg so the voice and video tests run) and confirm CI is green on the main branch.
2. Merge the extraction fix (short death and arrest rumours stay claims; the reply says how an unreadable message was read).
3. Merge the dataset branch (claims, official-releases snapshot, harness, builders). Retire the branch whose plain-image change was made redundant by the main branch.
4. Re-issue the Telegram token and put it only in the ignored `.env`.
5. Correct every sentence in `CLAUDE.md` and the README that overstates storage, official-site refusals or what was tested.

**Exit conditions:** CI is green on main; `python -m pytest -q`, `python scripts/check_no_fixtures.py` and `python tools/forge.py check` pass; `python scripts/preflight.py` is green on the machine
that will run the live system; no stale branch holds work that main lacks.

## Stage 1: safety before features

Packages: RK1, RK2, SG2 (no-store test), A4, F3 (storage wording).

Why first: the checkers store thumbnails, transcripts and text; the command centre lists every stored record without a sign-in; and the bot sends any photo to the image checker. Until this is fixed,
private-image help and call warnings must not be shown.

1. **Private mode.** A request marked private never writes a record. The bot marks every call it makes. Text that matches the private-image intent is never stored whatever the request says.
2. **Bot refuses private images.** After the intent is detected, or after the help command, the bot refuses photos, video and documents before downloading them.
3. **Sensitive-topic guard.** Communal, caste, religion-linked, election and political topics always end in "cannot tell" with a human-review flag.
4. Write the no-store tests first; they are the evidence for the storage statement in the README.

**Exit conditions:** tests post a photo, a voice note, a notice and a document in private mode and find zero stored rows, and the command centre lists none of them; a test with a fake Telegram shows
no image downloaded in help mode; a communal rumour is never refuted or supported by the system alone; the README says exactly what is stored and for what.

## Stage 2: the headline path works from the phone

Packages: A1, A3 (live check), A2, B4, C1.

1. **One agent path.** The bot and `POST /verify` run the same steps as the web page, including official text and the contrast check. Test with a fake model: a changed-number claim refutes from the bot.
2. **Official sources per network.** Probe every official source from the machine and network that will run the live system and record each result (reachable, refused, no usable feed). Add every source that
   answers the honest client; where a source refuses it, name the refusal in the result and use an official access route, never a disguised client. PIB needs release-page fetching because its feed carries titles only.
3. **Short-rumour check, live.** On the live machine send a short death or arrest claim in several wordings, with and without a question mark; confirm it reaches the checker, and that an unreadable message is
   described, not dismissed.
4. **Gemini load test.** Count model calls per message by purpose; run several simultaneous users; record what the user sees when the request ceiling is reached. Decide from the table: cut calls, queue
   with a plain "busy" message, or use a paid tier.
5. **Human reply layer.** The rules decide; the model writes only wording from a fixed facts object; seven guards reject any change of verdict, link, number, source or language; the template is the fallback.

**Exit conditions:** a changed-number claim refutes from the bot; the official-source table exists for the demo network; the short-rumour wordings all reach the checker; the load-test table and decision are in
`docs/gemini_limits.md`; every reply guard is shown rejecting a bad reply in a test, and two readers who did not write the layer agree its replies read like a person.

## Stage 3: measure

Packages: B1, B2, A5, B3, B5, A6.

Order matters here because the numbers are only honest if the rules are frozen before the final run.

1. **Run the dev split** with fact-check lookup off, then on. Read every wrong or abstained answer and sort it into buckets (extraction, retrieval, same-claim judge, contrast, web search, translation,
   genuinely unknowable). The bucket counts decide where effort goes next.
2. **Measure the rules that can say "true".** Add rows for rumours that outlets reported only to debunk them and for old events that match old headlines; run with web search on and off. Write the pass
   criteria before running them. Proposed, for the team to change first: the system may keep calling a claim true from two reputable outlets only if the false-support rate on the false and changed-number groups
   stays at or below one in thirty and no true claim is refuted. If the rule fails, tighten it (more outlets, a recency check) and rerun on dev.
3. **Tune thresholds on dev only**, and write each change into `config/thresholds.yaml` with its data and date.
4. **Add group F** (false claims from published fact-checks, two reviewers each) and the language rows (Hinglish, Tamil, Bengali, Marathi, Kannada).
5. **Freeze.** Tag the commit. Run the test split once. Whatever it says goes into the claim sheet, including a result worse than dev.
6. **Throughput of what the radar now is.** The India pulse (feeds read, items grouped, cost per item, a full refresh); the live stream worker when switched on (a sustained run with escalation on, then a
   record-and-replay at several speeds to find where it falls behind); media speeds; the funnel table. At least one escalation must run on a real item.
7. **Media under real conditions.** Re-run the image detectors on images that went through messaging-app compression, on screenshots, and on official satellite and weather images.

**Exit conditions:** the claim sheet holds false-refute, false-support, refute recall, coverage, correct abstention, precision by confidence, per-language rows and a Hindi-against-English comparison, each with n and a
range; the decision on the web-search rule is written beside its numbers; `docs/throughput.md` holds the five slide numbers with command, machine and network; the test split was run once on a tagged commit.

## Stage 4: help for citizens

Packages: RK3, RK4, RK5, RK6, SG1, SG3, SG5, SG6, SG7, C3, C2.

Run the two features side by side; both depend only on Stage 1.

**Raksha**
1. Guided flow: safety, for whom, under 18, what is happening, platform, record, summary, next steps; Hindi and English; the under-18 branch shows only the child route; unsure counts as under 18.
2. Report summary built from typed fields only, with no model call and no legal section numbers.
3. Recognition measured on a labelled set of messages, including near-misses that must not trigger; when unsure the bot asks.
4. People confirm every route and number on its official page with a date, and a native speaker reviews every Hindi sentence. A route that cannot be confirmed is removed.

**Scam Guard**
1. Script signals as plain code with a negation guard for genuine bank warnings; English, Hindi and Hinglish phrase groups in config.
2. The listening page with consent, one big button, three states, vibration, a spoken warning and the hang-up and help buttons; works on a real phone.
3. A labelled set of scam scripts and ordinary calls (including a bank's real "never share your OTP" warning); detection rate by type, how many chunks until the alert, and the false-alarm rate on ordinary calls.
4. End-to-end audio through a phone speaker; record delay, transcript quality and misses.
5. A native speaker reviews all Hindi and Hinglish phrases and the spoken warnings.

**Shared**
- Indian scam types and immediate next-step advice added to the next-steps config (digital arrest, bank or KYC, UPI tricks, fake customer care, electricity threats, job and task scams, loan-app harassment).
- The "where has this been reported" timeline: outlets, fact-checkers and live clusters in order, with the scope stated.

**Exit conditions:** a scripted Raksha conversation walks every branch with no step that asks for an image; every route shown is confirmed or removed; Scam Guard's detection rate, time to alert and false-alarm
rate are in the claim sheet and the false alarms were each read and explained; the no-store tests from Stage 1 still pass with these features on.

## Stage 5: officials and the wider picture

Packages: C5, A7, SG4, SG8, SG9, RK7, RK8, C4.

All tier C unless the earlier stages show a need.

- Seasonal topics ranked first in the officials view.
- Names and places in the contrast route, or an explicit "numbers only" statement.
- Family alert for Scam Guard (type and time only, no transcript); a weak voice-clone hint that never triggers an alert alone; after-the-call routes.
- A quiet help web page with a quick-exit button; the forget command and a delete button.
- Transcription model comparison on short Indian-accent clips recorded with consent.

**Exit conditions:** each item either meets its done-when or appears in the README's real-versus-roadmap table as "not built".

## Stage 6: proof and story

Packages: G1, G2, G3, G4, F5.

1. The pitch is built on **Radar, Verifier, Responder**, not on a detector. Every number on a slide is copied from the claim sheet or the throughput document with its command.
2. The README table says, row by row, what works, what is limited and what is not built. Its limits match the calibration notes.
3. Choose real, circulating, already fact-checked items in more than one language, plus one scam forward, one edited image, one AI-made image and one voice note. Include one case where the system says "cannot tell".
4. Prepare the responsible-AI slide (abstention, nothing filed, the ledger, what is stored and what is not) and the feasibility slide (cost per verdict, the request ceiling, what runs on the GPU machine and what could be hosted).
5. Run the full demonstration from a cold start twice in a row with no edits, on the network that will be used and on a second network. Keep screenshots of a good run as a labelled backup; never present them as live.
6. Final repository checks: tests, guard, ticket check, preflight, ledger verification, licence table, no secrets in history.

**Exit conditions:** every slide number is traceable; the README table matches the repository; two cold-start runs in a row pass without edits; each team member can explain any part of the system when asked.

## Dependencies at a glance

```
Stage 0 ──► Stage 1 ──┬──► Stage 2 ──► Stage 3 ──► Stage 6
                      ├──► Stage 4 ──────────────► Stage 6
                      └──► Stage 5 (optional) ───► Stage 6
```

- Stage 3 needs Stage 2's single agent path, or the numbers describe the web page and not the bot.
- Stage 4 needs only Stage 1, so it can run beside Stages 2 and 3.
- The people tasks (confirming routes, reviewing Hindi, choosing real items, a second labeller for the dataset groups that have one) have no code dependency and should start in Stage 0.

## What gets dropped first

1. Tier C packages, in this order: C4, C5, A7, SG9, SG8, RK8, RK7.
2. Then tier S packages from the lowest-value end: B5, C3 beyond the main scam types, C2, A6 beyond the languages already working, B2, RK4, SG4, SG6.
3. Never dropped: Stage 0, Stage 1, A1, A4, A5, B1, B3, B4, RK3, RK5, SG1, SG3, SG5, SG7, the limits statements, and the native-speaker review of anything shown in Hindi (if it cannot be reviewed, show English only).

## Verification commands

```
python -m pytest -q                                  # unit tests (live-internet tests are excluded by default)
python -m pytest -q -m contract                      # live contract tests, needs the keys in the ignored .env
python scripts/check_no_fixtures.py                  # no mock or hardcoded data in src/
python tools/forge.py check                          # ticket graph
python scripts/preflight.py                          # every key name, source, service, index and the ledger
python scripts/probe_sources.py                      # reachability of every configured source from this network
python scripts/verify_ledger.py                      # the chain is intact
python scripts/eval/run.py --split dev --factcheck off --official snapshot     # the dev table
python scripts/eval/run.py --split dev --factcheck on  --official snapshot     # coverage when a fact-checker exists
python scripts/eval/run.py --split test ...          # once, on a tagged commit
python scripts/bench_pipeline.py                     # to be written in Stage 3
python scripts/load_gemini.py                        # to be written in Stage 2
python scripts/guard_eval.py                         # to be written in Stage 4
```

## Decisions that need a person

| Decision | Why it matters |
|---|---|
| Fresh Telegram token, typed into the ignored `.env` | The old one was exposed |
| Paid tier for the language model, or accept the free-tier request ceiling | Decides how many people can use the bot at once |
| Any model download (a larger transcription model, audio or NLI models) | Licences and disk |
| Use of official sites that refuse the client | Only an official access route or a network where the honest client answers; never a disguised client |
| Who answers "is it lawful to listen to a call on speaker?", and whether Scam Guard is shown publicly | The plan does not claim legal clearance |
| Who reviews the Hindi wording, and who is the second labeller for the dataset groups that have one | Quality and credibility of every number and sentence |
| Pass criteria for the web-search rule, written before the test split is run | Prevents tuning to the answer |

## Definition of done for any package

Code or content exists; unit tests pass, including a failure case; wording and numbers live in `config/`; the guard, the ticket check and the full suite pass; the README or claim-sheet line that
mentions it is true and carries its command; anything not tested is written down as not tested.
