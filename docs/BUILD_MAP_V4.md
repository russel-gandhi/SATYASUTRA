# Build map (v4): what SATYASUTRA is, what exists, what is left to build

This map replaces the scope, ownership and package lists in `BUILD_MAP_FINAL.md`. It describes the product as it stands in the repository, names every remaining
package with its files and its "done when", and states what may and may not be claimed. The order of work is in [`BUILD_PLAN_V4.md`](BUILD_PLAN_V4.md). The design
rules in `docs/plan/BIBLE.md` still hold (two verdict axes, only Tier 1 or 2 evidence decides a claim, "cannot tell" is a valid answer, no mock data in `src/`,
never file or send anything on anyone's behalf).

## 1. The product in one picture

A civic misinformation platform with three layers and two front doors.

```
 Citizen: Telegram bot, web page                      Officials: command centre, officials view, evidence graph
   "Is this true?"  "Is this a scam?"  "I need help"     what is spreading, where it started, verdict beside it
        text, link, PDF, screenshot, photo, voice note, video                       ▲
                     │                                                              │
                     ▼                                                              │
   ┌──────────────────────────────  SATYASUTRA engine  ───────────────────────────────┐
   │ RADAR      India pulse: Google Trends India, fact-checker feeds and Indian outlet  │
   │            headlines in several languages, grouped with no model calls; an optional │
   │            live Bluesky stream worker is kept, off by default                       │
   │ VERIFIER   spends model and forensic budget only on what spreads or is asked:      │
   │            claim agent (extract, fact-checks, official text, web search,           │
   │            Wikidata, same-claim judge, rules), media checks (edits, AI-made,       │
   │            fact-check history, video frames), voice to text, link and notice checks│
   │ RESPONDER  plain-language verdict with sources, correction card, next steps and    │
   │            evidence pack, guided help for private-image abuse, live call warning  │
   │ LEDGER     every verdict hash-chained; explanations show what decided the label    │
   └────────────────────────────────────────────────────────────────────────────────────┘
```

Detectors are one tool inside the Verifier. They are not the product.

## 2. What exists (read from the repository)

| Area | State | Honest limit |
|---|---|---|
| Claim agent (LangGraph): extraction, Fact Check lookup, same-claim judge, Wikidata grounding, rules, ledger | Built | `SUPPORTED` rarely appears; many claims end in "cannot tell" |
| Official text and contrast refutation (RBI and SEBI feeds, FTS5 index, domain gate) | Built, thin | Refutes a changed number only; names and places are not covered; few releases held |
| Web search (Google News search; a model only labels each headline, code decides from counts) | Built | Two reputable outlets can support a claim; one fact-checker or two outlets can debunk. Lists and counts are policy choices, not calibrated. An outlet reporting a rumour can be labelled as fact, and old events can match old headlines |
| Local NLI fallback judge for "is this the same claim?" when the language model cannot answer | Built | Stricter than the model; not measured on a labelled set |
| India pulse: 23 live feeds in three lanes (search trends, fact-checker feeds, outlet headlines in several languages), grouped and ranked | Built; replaced Bluesky as the "what is spreading" source | A view of Indian media and search attention, not on WhatsApp; clusters in different languages are not matched to each other; thresholds are policy choices |
| Fast clock, clusters and escalation on the live Bluesky stream | Built, off by default | Escalation has not been shown on a real item; no benchmark exists |
| Officials view, command centre, evidence graph, Trust Report, citizen history, case workspace | Built | Computed from records this instance stored, not nationwide data. The command centre has no sign-in and lists every stored record |
| Image edits (TruFor), AI-made images (Smaug), debunked-image index, video frames | Built | Small calibration sets; edits after compression are missed; face swaps not detected |
| Plain-language verdict and image result | Built | Hindi wording unreviewed |
| Voice notes to text (Whisper, GPU first, CPU fallback), Kannada second opinion | Built | Hindi has word errors |
| Synthetic-speech check (wav2vec2 classifier) | Built, weak | Suspicion label only, never "real voice"; 19 of 19 synthetic clips caught and 0 of 20 real flagged on a small, weak set with no cloned real people |
| Telegram bot (text, voice, photo), correction cards, next steps, evidence pack | Built | See known problems below |
| Lookalike-link check, notice checker, link and PDF inputs | Built | Link names only; real photographed notices lightly tested |
| Explanations (exact Shapley credit per evidence item and per detector) | Built | Model steps are not attributed |
| Resilience (retry, fallback model, "model busy" message), preflight, start script | Built | The free-tier request ceiling is the limiting factor |
| Raksha: intent detection and seven guidance routes | Built, guidance text only | No guided flow; see the Raksha and Scam Guard plan |
| Live call warning | Not built | See the Raksha and Scam Guard plan |

**Known problems the packages below fix:**
1. The image, voice, notice, document and claim checkers store a thumbnail, transcript or text on the server, and the command centre lists every stored record with no sign-in. This contradicts the statement that Raksha never stores anything. A delete route for a record exists, but nothing calls it for the bot's users.
2. The Telegram bot calls `POST /verify`, which has no official-text step; only the web page's agent route runs the contrast check.
3. A death or arrest rumour in plain words ("X is dead") was once dropped as chatter by the extraction step. An open pull request adds a safety net and a reply that says how the message was read; web search now answers such claims when outlets carry them.
4. Continuous integration fails because the runner has no ffmpeg. The fix is in an open pull request.
5. The verdict dataset and its harness live on an unmerged branch, so no accuracy number exists yet.
6. Some official sites answered 403 from one machine's network and 200 from another with the same honest client; the recorded "refuses our client" notes describe a network, not the client.
7. The web-search verdict rules (two outlets support, one fact-checker debunks) have not been measured, so their false-support rate is unknown.

## 3. Packages

Tier: **M** must land, **S** should, **C** could. Files marked *integrator* are edited only by the session that owns the shared files; others send a patch.

### A. Truth quality

| ID | Package | Tier | Files | Done when |
|---|---|---|---|---|
| A1 | **Bot uses the agent path.** The Telegram bot and `POST /verify` run the same steps as the web page, including official text and contrast | M | `bot/telegram.py`, `pipeline.py` *integrator* | A changed-number claim refutes from the bot exactly as on the web page; test with a fake model |
| A2 | **More official text.** Probe and add every reachable official source (PIB release pages, IMD, NDMA, Election Commission, data.gov.in, health ministry); each behind the domain gate; failures are named, never hidden | S | `config/runtime.yaml`, `config/policy.yaml`, `sources/official.py` | Probe table shows each source's state per network; at least one new source is indexed and used in a dataset row |
| A3 | **High-stakes extraction.** Short death, arrest, attack and resignation rumours are kept as claims; the reply says how an unreadable message was read | M | open pull request | Merged; one live check on the demo machine shows "X is dead" reaches the checker |
| A4 | **Sensitive-topic guard.** Communal, caste, religion-linked, election and political topics always end in "cannot tell" with a human-review flag | M | `config/`, decide step *integrator* | A communal rumour never returns refuted or supported from the system alone |
| A5 | **Measure the rules that can say "true".** `SUPPORTED` now comes from positive fact-check ratings and from two reputable outlets; measure how often a false claim is called true, including rumours that outlets reported only to debunk them; keep the confidence cap | M | `claims/web.py`, `claims/rules.py`, `config/policy.yaml` | The false-support rate with its range is in the claim sheet; the decision to keep or tighten the rule is written next to it |
| A6 | **Languages measured.** Hinglish, Tamil, Bengali, Marathi, Kannada claims run through the dataset; one row per language | S | `tests/eval/authored.yaml` | Per-language row in the claim sheet with n and a range; only listed languages are named in the pitch |
| A7 | **Names and places in contrast.** Extend the contrast route beyond numbers, or state that it is numbers only | C | `claims/contrast.py` | Either the rows for changed names and places refute, or the README says numbers only |

### B. Measurement

| ID | Package | Tier | Files | Done when |
|---|---|---|---|---|
| B1 | **Verdict dataset and harness.** Merge the dataset branch; run the dev split with fact-check lookup off and on; read every wrong answer into error buckets | M | `tests/eval/`, `scripts/eval/`, `docs/claim-sheet.md` | Claim sheet holds false-refute, false-support, refute recall, coverage, correct abstention and precision by confidence, each with n and a range |
| B2 | **Group F.** Collect false claims from the Fact Check API; two people review each; add the accepted rows | S | `scripts/eval/build_f.py`, `tests/eval/f_rows.jsonl` | F rows exist with two labellers each |
| B3 | **Throughput benchmark of what the radar now is.** For the India pulse: feeds read, items grouped, cost per item, how long a full refresh takes. For the live stream worker when switched on: a sustained run with escalation on, and a record-and-replay at several speeds to find where it falls behind. Media speeds. The funnel table (items in, groups, escalated, decided, cache hits) | M | `scripts/bench_pipeline.py`, `scripts/record_stream.py`, `docs/throughput.md` | Five slide numbers exist, each with its command, machine and network; at least one escalation has run on a real item |
| B4 | **Gemini load test.** Calls per message by purpose, error rates at several simultaneous users, what the user sees when the ceiling is hit | M | `scripts/load_gemini.py`, `llm/gemini.py` counter *integrator* | A table and a decision recorded in `docs/gemini_limits.md`; the bot shows a plain "busy" message instead of hanging |
| B5 | **Media under real conditions.** Re-run the image detectors on images after messaging-app compression, on screenshots, and on official satellite and weather images | S | `scripts/`, `docs/calibration/` | One line per detector per condition |

### C. Citizen experience

| ID | Package | Tier | Files | Done when |
|---|---|---|---|---|
| C1 | **Human reply layer.** The rules decide; a model writes only the wording from a fixed facts object; seven guards reject any reply that changes the verdict, adds a link, number or source, or uses the wrong language; the template is the fallback | S | `reply/compose.py`, `config/prompts/reply.txt`, `config/reply.yaml` | Each guard is shown rejecting a bad reply in a test; readers who did not write it agree the replies read like a person |
| C2 | **Where it has been reported.** A timeline of which outlets, fact-checkers and live clusters carry a claim, with the scope stated | S | `sources/spread.py`, UI panel | A claim several outlets carried returns an ordered timeline; an unreported claim says how far back it looked |
| C3 | **Indian scam types.** Intents and immediate next-step advice for digital arrest, bank or KYC calls, UPI tricks, fake customer care, electricity threats, job and task scams, loan-app harassment | S | `config/actions.yaml`, `act/plan.py` | Each type returns the right routes and evidence pack; routes confirmed on official sites or removed |
| C4 | **Voice on Indian speech.** Compare transcription models on short Indian-accent clips recorded with consent | C | `voice/` | A table of clips and errors; the choice and its size are written down |
| C5 | **Seasonal topics.** Festival, election, exam, cyclone, flood and heat terms ranked in the officials view | C | `config/runtime.yaml` | Matching clusters are listed first |

### D. Raksha (guided help for private-image abuse)

Full design in [`PLAN_RAKSHA_SCAM_GUARD.md`](PLAN_RAKSHA_SCAM_GUARD.md).

| ID | Package | Tier | Done when |
|---|---|---|---|
| RK1 | Private mode on the server: private requests never write a record (so nothing reaches the command centre), and intimate-intent text is never stored | M | Tests post a photo, a voice note and a notice in private mode and find zero rows, and the command centre lists none |
| RK2 | The bot never takes a private image: in help mode it refuses photos, video and documents before downloading | M | A fake-Telegram test shows no download and no API call for an image in help mode |
| RK3 | Guided flow: safety, for whom, under 18, what is happening, platform, record, summary, next steps; Hindi and English | M | A scripted test walks every branch; the under-18 branch never shows an adult-only step |
| RK4 | Report summary generator from typed fields only, no model | S | Nothing outside the typed fields can appear |
| RK5 | Every route and number confirmed on its official page, with a date; every Hindi sentence reviewed by a native speaker | M | Each shown route is confirmed or removed |
| RK6 | Recognition measured on a labelled set of messages, including near-misses that must not trigger | S | Triggered share and false triggers reported with ranges |
| RK7 | Quiet web page with a quick-exit button, no history, no analytics | C | Works at phone width; stores nothing |
| RK8 | Forget command in the bot; a visible delete button on the web page for the record deletion route that already exists | C | State is gone after the command; the button removes the record |

### E. Scam Guard (a warning during a scam call)

| ID | Package | Tier | Done when |
|---|---|---|---|
| SG1 | Script signals as plain code with a negation guard for genuine bank warnings; config in English, Hindi and Hinglish | M | Unit tests per signal and per state; "do not share your OTP" never alerts |
| SG2 | `POST /guard/chunk` with in-memory sessions and expiry | M | A test proves nothing is written to disk or to any record |
| SG3 | The `/guard` page: consent, one big button, three states, vibration, spoken warning, hang-up and help buttons | M | Tried on a real phone and on a desktop |
| SG4 | Optional family alert through the bot, carrying type and time only | S | A second phone receives it; it contains no transcript |
| SG5 | Labelled evaluation set of scam scripts and ordinary calls; harness | M | Detection rate by type, chunks until alert, and false-alarm rate on ordinary calls, each with a range |
| SG6 | End-to-end audio test through a phone speaker | S | A table of runs: delay, transcript quality, alert or miss |
| SG7 | Native-speaker review of all Hindi and Hinglish phrases and spoken warnings | M | Reviewer's name and date in the config header |
| SG8 | Voice-clone hint as a weak extra line, never the trigger | C | Appears only together with a script signal |
| SG9 | After-the-call help from the Guard into the existing money-lost route | C | Each scam type returns the right routes |

### F. Repository health

| ID | Package | Tier | Done when |
|---|---|---|---|
| F1 | Merge the CI fix and the extraction fix; confirm CI is green on the main branch | M | Green check on main |
| F2 | Merge the dataset branch; retire the obsolete plain-image branch | M | One branch of truth |
| F3 | Correct `CLAUDE.md` and the README where they overstate storage, official-site refusals or what is tested | M | Every claim in the README table matches a command or a recorded result |
| F4 | Re-issue the Telegram token; keys only in the ignored `.env` | M | Old token revoked |
| F5 | Licence table complete for every model, font and dataset used | S | `docs/licences.md` lists what was and was not read |

### G. Story and proof

| ID | Package | Tier | Done when |
|---|---|---|---|
| G1 | Pitch built on Radar, Verifier, Responder with only claim-sheet numbers | M | Every number on a slide has its command |
| G2 | README real-versus-roadmap table | M | Each row has its evidence or says "not built" |
| G3 | Demo items: real, circulating, already fact-checked, in more than one language; one scam forward; one edited image; one voice note | M | Chosen and rehearsed |
| G4 | Responsible-AI and feasibility slides | S | Abstention, no filing, ledger, storage statement, cost per verdict, hosting split |

## 4. Ownership

| Owner | Files |
|---|---|
| Integrator session | `api/app.py`, `pipeline.py`, `media.py`, `video.py`, `llm/gemini.py`, `config/*.yaml`, `forensics/service.py`, `ui/index.html`, `CLAUDE.md` |
| Any session on its own branch | new modules (`raksha/`, `scam/script.py`, `reply/`, `sources/spread.py`), new pages (`ui/guard.html`, `ui/help.html`), `scripts/`, `tests/` |

Rule: one branch per package from the main branch, merged by the integrator; no one edits another owner's files without sending a patch.

## 5. Guardrails (each is a test or a config rule, not a promise)

- A check that finds nothing is "nothing found", never "genuine" or "authentic". One detector alone is a suspicion.
- Communal and political topics end in "cannot tell" and a human-review flag.
- Nothing is filed, sent or called on anyone's behalf. Our client states honestly what it is.
- Private-image help never accepts, stores, hashes or analyses an image; the server stores nothing from it.
- Call warnings keep no audio and no transcript on the server and never say a call is safe.
- Every route, number and helpline is confirmed on its official page with a date, or removed.
- Hindi and other non-English wording is reviewed by a native speaker before it is shown.

## 6. What may and may not be claimed

- May: what the claim sheet, the throughput document and the README table state, with n, ranges, machine and network named.
- May not: "all of India", a language count beyond those measured, legal clearance, removal of content, "real-time at scale", detection of cloned voices or face swaps, or any statistic the team has not re-read from its source.

## 7. Approvals only the user can give

Fresh Telegram token typed into the ignored `.env`; the decision on official sites that refuse the client (use only an official access route, or the honest client from a network where it answers, never a disguised client); any model download (a larger transcription model, an audio or NLI model); the paid-tier decision for the language model; the legal question about listening to a call on speaker; who reviews Hindi wording; whether the call warning is shown publicly.

## 8. Roadmap, stated as not built

WhatsApp intake, cross-platform spread beyond the live stream, face-swap and AI-video detection, counselling and takedown handling, legal advice, investment-tip checks against a regulator register, a human review queue for communal content, a group guardian bot, a newsroom export.
