# Build plan: clock-time schedule to the deadline

Written Sat 31 Oct 2026, 13:15 IST. Deadline **Mon 2 Nov 2026, 00:00 IST** (midnight at the end of Sunday 1 Nov). The packages (IDs like C1, A2), their owners, files and
"done when" are in [`BUILD_MAP_FINAL.md`](BUILD_MAP_FINAL.md). This file says **when**, **in what order**, and **what gets cut if a
checkpoint fails**. Times are targets; checkpoints are fixed. If a block runs late, cut by the rules below, never move a checkpoint.

## People and sessions

| Who | Machine | Runs |
|---|---|---|
| **Person 1, integrator** | Friend's machine (RTX 4060, `.env` keys, `pdqhash`, TruFor and Smaug service) | Its Claude session does P0, P1, I1, I2, I3; merges every branch; runs every live check and the demo |
| **Person 2** | Own machine, one Claude session | Lane A (official text, contrast, scam links, notice checker) |
| **Person 3** | Own machine, one Claude session | Lane C (bot, card, Verify → Act, Raksha-lite), then lane B work if lane B has no owner |
| Lane B | Whoever is free first, or a third Claude session on person 2 or 3's machine | Voice notes, evaluation, audio (stretch) |

Each Claude session gets `BUILD_MAP_FINAL.md`, `CLAUDE.md` and its package ID as its brief. Code written away from the integrator
machine is tested with unit tests there and live on the integrator machine after merge.

## Before anything else (13:15 to 13:45)

1. **User:** create a fresh Telegram token with BotFather and type it into `.env` on the integrator machine. Do not paste it in chat.
2. **User:** approve the downloads listed in section 7 of the map that are needed today: faster-whisper model, Devanagari font.
   The audio and NLI models can wait until checkpoint CP2.
3. **Team decision:** Raksha-lite (guidance only, no uploads) changes Bible D52, which keeps all of Raksha on the roadmap.
   Agree it or drop S5. Until agreed, S5 content work may start but no code.
4. **Integrator (P0):** merge `fix/review-ledger-schema-tickets` into `phase-8/prototype-ui`; commit these two files to `docs/`;
   mark `docs/PLAN_TO_DEADLINE.md` superseded; restore Bible revision 3 from the user's local stash (`stash@{1}` on the user's
   machine holds `docs/plan/BIBLE.md` with D56 to D67).
5. Everyone pulls `phase-8/prototype-ui` and branches from it.

## Schedule

### Block 1: Sat 13:45 to 17:30, the gateways

| Integrator | Lane A | Lane C | Lane B | Non-code (a teammate between tasks) |
|---|---|---|---|---|
| P1 Smaug label fix, media through the contract, licence, satellite test (1.5 h) then I2 reliability (start) | A1 PIB and IMD adapters, FTS5 index, domain gate (3 h) | C1 Telegram bot (3 h) | B1 Whisper voice notes (2 h), then E1 set-up | Official-domain list with sources for S2; helplines and portal routes with source URLs and checked dates for S1 and S5; start collecting real demo items |

**Merge window 17:30 to 18:00.** Integrator merges A1, C1 and B1 and wires them (I3, first pass).

**Checkpoint CP1 (18:00), run on the integrator machine:**
- A forwarded text and a forwarded voice note to the bot each get a verdict reply.
- `probe_sources.py` shows PIB and IMD reachable, or the replacement official source is recorded.
- Preflight runs (it may still show amber items).

If CP1 fails: lanes A and C stop new work and pair with the integrator until it passes.

### Block 2: Sat 18:00 to 22:00, the differentiators

| Integrator | Lane A | Lane C | Lane B |
|---|---|---|---|
| I1 finish: officials' view and disaster topics first, on top of `7f6036b` (2 h); then I2 reliability | A2 contrast refutation (3 h), then S2 lookalike links (2 h, starts in Block 3 if late) | C2 correction card (2 h), then S1 Verify → Act (4 h, runs into Block 3) | E1 evaluation script and the first numbers; then B2 audio only if approved and CP1 passed on time |

**Merge window 21:30 to 22:00.**

**Checkpoint CP2 (22:00), go or no-go for the social work:**
- Contrast refutation fires on a live PIB passage with a changed number, with fact-check lookup switched off.
- The bot returns a correction card in Hindi for a refuted item.
- A voice note goes through to a verdict.

If CP2 passes: Block 3 runs as written. If it fails: **cut S4 (notice checker) and S5 code (Raksha-lite becomes a roadmap slide with
the content already written), cut B2 and A3**, and Block 3 finishes CP2 instead.

### Block 3: Sat 22:00 to Sun 02:00, social flows

| Integrator | Lane A | Lane C | Lane B |
|---|---|---|---|
| I2 finish (retry, fallback model, outage tests, start script); I3 wiring of A2, C2 and S1 | S2 lookalike links; then S4 notice checker | S1 Verify → Act finish; then S5 Raksha-lite | B2 audio (if go) or E1 |

**Merge window 01:30 to 02:00.** Everything merged so far must pass the suite and the guard on the integrator machine.

### Sleep, Sun 01:00 to 10:00, staggered

| Person | Sleeps |
|---|---|
| Person 2 | 01:00 to 06:00 |
| Person 1 (integrator) | 02:30 to 07:30 |
| Person 3 | 05:00 to 10:00 |

Someone is always awake. A Claude session may keep working on a ticketed package during its owner's sleep only if the owner left it
a written brief and it pushes to its own branch; nobody merges while asleep.

### Block 4: Sun 07:30 to 14:00, finish and harden

- Integrator: merge all branches; preflight green; outage drills (Gemini 429, Fact Check down, PIB down, TruFor service off, bad
  upload, injected instruction, a brand-new claim); fix what breaks.
- Lane A: S4 finish or cut; A3 NLI only if everything above is done.
- Lane C: S5 finish; E2 README first draft.
- Lane B: E1 numbers into `docs/claim-sheet.md` (refute-without-fact-check rate, false-refute rate, per-language rows, TruFor and
  Smaug rows); B2 finish or cut.

**Checkpoint CP3 (14:00): feature freeze.** After this, only fixes. Whatever is not done is written into the README's roadmap table
as "not built", with the reason.

### Block 5: Sun 14:00 to 18:00, story and proof

- All: choose demo items (real, circulating, already fact-checked; one Hindi item, one second-language item, one scam forward, one
  edited image, one AI image, one voice note).
- Pitch deck: user and victims, one sourced figure (Bible Q21: re-read it from the original), live demo, numbers, feasibility and
  scale (cost per verdict, Gemini limit, VM-plus-GPU split), responsible AI, roadmap, QR code to the bot.
- E2 README final: what is real vs roadmap, licences and credits, how to run.
- Screenshots of a good run as a network-failure backup.

### Block 6: Sun 18:00 to 22:00, rehearse

- Two full runs in a row from a cold start (`start_demo.ps1`, `preflight.py`), on the venue network and on a phone hotspot.
- Everyone answers questions about a lane they did not build (the event rules require every member to explain the code).
- Fixes only. A fix that touches a shared file goes through the integrator.

### Block 7: Sun 22:00 to 23:30, ship

- Final merge to `main` with the suite, guard and `forge.py check` green; tag `prototype-final`; check CI on the push.
- `verify_ledger.py` on the demo ledger prints "chain intact".
- Confirm the Telegram token in use is the re-issued one.
- Submit by 23:30. The last 30 minutes are buffer.

## Demo (about 4 minutes, all live)

| Time | Show | Proves |
|---|---|---|
| 0:00 | One sentence on who it is for. A forward that changes one number in a PIB statement goes to the bot; fact-check lookup off; contrast pair; `REFUTED`; correction card in Hindi | Refutes before fact-checkers; answers in the group |
| 0:50 | A Hindi voice note rumour: transcribed, checked, answered | Voice-first, Indian languages |
| 1:20 | A scam forward with a lookalike scheme link: flagged, real domain shown, Verify → Act gives the 1930 route and an evidence pack | Citizen gets the next step, not just a label |
| 2:00 | An edited image (TruFor heatmap) and an AI image (Smaug, "suspected"); a video's per-frame lines | Deepfake audit with honest limits |
| 2:40 | Officials' view: a spreading cluster, its first post and variant diff, disaster topics first | Viral spread and origin |
| 3:10 | Robustness: an injected instruction ignored, a source switched off, a brand-new claim answered "cannot tell yet" | Never guesses |
| 3:30 | Claim sheet numbers, `verify_ledger.py`, then the QR code: judges forward their own items | Measured, tamper-evident, testable |

Raksha-lite, if built, is shown as one screen of the guidance flow with no image involved.

## Cut order (if time runs out, cut from the top)

1. X1 spoken reply
2. A3 NLI second check
3. B2 audio deepfake (slide says "roadmap, not built")
4. S5 Raksha-lite code (keep the content as a slide)
5. S4 notice checker
6. S2 lookalike links (Verify → Act still routes scam forwards)
7. Officials' view polish in I1 (keep first post and diff)

**Never cut:** C1 bot, C2 card, A1 official text, A2 contrast refutation, B1 voice notes, S1 Verify → Act, I2 reliability, E1
headline numbers, E2 README, the ledger check, two rehearsals.

## Added packages (31 Oct, 15:50)

[`SPEC_REPLY_SPREAD_EVAL.md`](SPEC_REPLY_SPREAD_EVAL.md) adds R1 human replies, R2 news-spread timeline, R3 Gemini load test and D1 the verdict dataset and harness. R3 runs first, D1 starts at once with a different person, and D1's first dev table is part of CP2 (22:00).

## India fit (31 Oct, 16:35)

Makes the prototype fit how Indians actually receive and act on forwards. About 11 hours in total, all small and mostly config or tests, so they can
run in parallel with the packages above. Detail and reasoning: the "how can we fit it in the Indian context" discussion; the rules below are what binds.

| ID | Package | Hours | Owner | Files | Done when |
|---|---|---|---|---|---|
| IN1 | **Languages measured, not assumed.** Add Hinglish (Hindi in English letters), Tamil, Bengali and Marathi claims to the verdict dataset (about 28 rows: true, changed-number, unknowable) and run them. Hinglish drafts can be written by the team; the other three need a reader to check them | 2 | dataset owner (D1) | `tests/eval/authored.yaml`, claim sheet | The claim sheet has one row per language with n and a range; the pitch names only languages that appear there |
| IN2 | **Indian scam types and first-hour advice.** Intents for digital arrest, bank or KYC update, UPI collect-request and QR tricks, fake customer-care numbers, electricity-disconnection threats, part-time job or task scams, loan-app harassment and sextortion; each with plain Hindi and English advice and a source URL with a checked-on date | 1.5 | Verify-to-Act owner | `config/actions.yaml`, `act/plan.py` word lists moved to config | A forward of each type returns the right intent, the right routes and the evidence pack; routes stay marked unconfirmed until opened on an official site |
| IN3 | **More official text.** Probe IMD, NDMA, the Election Commission, data.gov.in (with the key), the health ministry and a few state sites; add every one that answers our client as a Tier 1 feed behind the domain gate. Record the ones that refuse (403) and do not disguise the client | 2 | lane A | `config/runtime.yaml`, `config/policy.yaml`, `docs/probe_results.md` | The probe table shows the new sources; at least one is indexed; T and C rows can be built from it |
| IN4 | **Regional-language fact-check search.** Search the Fact Check API with the original-language text as well as the English rendering, and read regional fact-checker feeds in their own language; compare with the earlier 0 hits in 4 for Hindi | 2 | lane A | `sources/factcheck.py` callers, config | Hit rate on the Hindi and Hinglish rows is measured and written down |
| IN5 | **Sensitive-topic guard** (Bible D3). Communal, caste, religion-linked, election and political topic terms, in English and Hindi, force `UNVERIFIABLE_YET` with a "needs a human reviewer" flag and the calmer wording; the officials' view marks them | 1 | integrator (rules, app) with a config list from anyone | `config/` list, `claims/rules.py` or the agent's decide step | A communal rumour test never returns REFUTED or SUPPORTED from the system alone; the officials' view shows the flag |
| IN6 | **Voice on Indian speech.** Compare Whisper small with a larger model on about 10 short Indian-accent Hindi and English clips recorded by the team with consent; keep what is clearly better and fits the GPU | 1.5 | lane B | `voice/`, `docs/` | A short table of the clips and the errors seen; the choice and its size are written down. Needs the user's approval to download a larger model |
| IN7 | **Seasonal topics in the officials' view.** Festival, election, exam, cyclone, flood and heat terms in English and Hindi, ranked like the disaster words | 0.5 | integrator | `config/runtime.yaml` | The officials' view lists them first when a cluster matches |

**Order:** IN2, IN5 and IN1 first (cheap and visible), then IN3 and IN4, then IN6, then IN7. Finish all by CP3 (Sun 14:00). **Cut order if short:** IN7, IN6,
IN4, IN3. Never cut IN5: it is the guardrail that makes the demo safe on the topics judges are most likely to try.

**Say and do not say (pitch and README).**
- Say: built for Indian forwards, tested in the languages the claim sheet lists, on the official sources that answered our client, with Telegram working today and WhatsApp as the next channel.
- Do not say: "for all of India", "supports 22 languages", anything about legal admissibility or legal clearance, or any statistic about forwards, scams or losses that the team has not re-read from its original source (Bible Q21). Data handling is stated as "designed to store nothing but the verdict and its ledger entry"; a lawyer has not reviewed it against India's data protection law.
- WhatsApp: do not claim it. Offer the Telegram bot, and show a "share to the bot" route; WhatsApp business onboarding is on the roadmap slide.
- Elders and low literacy: replies stay short, one action first. A spoken Hindi reply and Call Guard are stretch items.

## Status log

Update at each checkpoint: time, who ran it, pass or fail, what was cut.

| Checkpoint | Time | Run by | Result | Cuts |
|---|---|---|---|---|
| CP1 gateways | | | | |
| CP2 differentiators | | | | |
| CP3 feature freeze | | | | |
| Rehearsal 1 | | | | |
| Rehearsal 2 | | | | |
| Submitted | | | | |
