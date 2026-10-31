# Build map (final): what we build, who owns it, and what "done" means

Written Sat 31 Oct 2026, 13:15 IST, against branch `phase-8/prototype-ui` at `7f6036b` plus `fix/review-ledger-schema-tickets`.
Deadline: **Mon 2 Nov 2026, 00:00 IST** = midnight at the end of Sunday 1 Nov (about 35 hours). The clock-time schedule is in [`BUILD_PLAN.md`](BUILD_PLAN.md).

This file supersedes the scope and ownership parts of `docs/PLAN_TO_DEADLINE.md` and `docs/plan/BUILD_MAP_V3.md`. The design
rules in `docs/plan/BIBLE.md` still hold (two verdict axes, Tier 1 or 2 decides, "cannot tell" is a result, no mock data in `src/`,
never file on anyone's behalf). Hours are estimates, not measurements.

## 1. The product in one picture

One verification engine, two front doors.

```
 Citizen (Telegram bot, web page)                     Officials (dashboard)
   "Is this true?"  "Is this a scam?"                   what is spreading now, where it started,
   "Is this notice real?"  "I need help"                verdict and correction card ready
          │  text, screenshot, photo, voice note, video          ▲
          ▼                                                      │
   ┌───────────────────────── SATYASUTRA engine ──────────────────────────┐
   │ Whisper (voice→text) · Gemini (extract, translate, OCR)              │
   │ Claims: Fact Check registry · PIB/IMD official text · contrast       │
   │         refutation · Wikidata · same-claim judge · rules             │
   │ Media:  debunked-image index · TruFor (edits) · Smaug (AI images)    │
   │         · video frames · audio check (experimental)                  │
   │ Scam:   lookalike-link check                                         │
   │ Fast clock on the Bluesky firehose: clusters, velocity, origin       │
   │ Ledger: every verdict hash-chained                                   │
   └──────────────────────────────────────────────────────────────────────┘
          │
          ▼
   Verdict + why + sources  →  correction card (post it back in the group)
                             →  "what to do now" + evidence pack (1930, cybercrime.gov.in, StopNCII)
```

**Pitch line.** Every false forward has a victim: the grandmother who is scammed, the village that panics over a fake dam break,
the girl whose photo is morphed. SATYASUTRA meets each of them in the chat where the forward arrived, in their language, with the
truth and the next step.

## 2. Where we are (checked 31 Oct, 13:00)

| Area | State | Evidence |
|---|---|---|
| Foundation, ledger, config, guard, CI | Done | Phase 0 and 1 tags; 262 tests pass on the fix branch (16 more need `pdqhash`, built on the integrator machine) |
| Claim agent (LangGraph): extract, Fact Check, Wikidata, same-claim judge, rules, ledger | Done | `agent/graph.py`, live trace over SSE; first live `REFUTED` (vaccine microchip claim) |
| Hindi | Works through an English rendering | `tests/contract/test_live_hindi.py` |
| Fast clock on the Bluesky firehose (~11 posts/s), clusters, cache, escalation | Done; escalation never fired live | `fastclock/` |
| Debunked-image index (117 items, PDQ), TruFor edits, Smaug AI images, 8-frame video | Done, small calibration sets | `docs/calibration/` |
| One-page web UI | Done | `ui/index.html` |
| **Telegram bot, correction card** | Not started | |
| **Official text (PIB/IMD), contrast refutation** | Not started | the gate's `allowed_domains` is read but not enforced |
| **Voice notes, audio deepfake** | Not started | |
| Origin and spread view | Done, in memory (not persisted) | `7f6036b`: first post, spread timeline, wording versions with diffs, claim-to-cluster match, UI panel |
| Social flows (scam, notice, Raksha-lite) | Not started | |
| Evaluation, claim sheet, README | Not started | |

Overall about **50%** of the planned core. The demo path for a claim and an image already works end to end.

## 3. Problem statement coverage after this map

| Statement says | Answered by | Package |
|---|---|---|
| Real-time, high-throughput | Firehose fast clock with live counters; cached verdicts | done, I2 |
| Live social feeds | Bluesky firehose; Telegram forwards | done, C1 |
| Multimodal media | Text, screenshot, photo, **voice note**, video | done, B1, C1 |
| Viral claims | Clusters by velocity; **origin and spread** | done, I1 |
| Verified database registries | Fact Check API; **PIB, IMD** official text | done, A1 |
| Knowledge graphs | Wikidata grounding; graph in the spread view | done, I1 |
| Forensic models, synthetic media | TruFor, Smaug, video frames, **audio (experimental)** | done, B2 |
| Trace provenance | Debunked-image index, **origin and first post**, Lens/TinEye links | done, I1 |
| Theme: public services, citizen tools | **Notice checker, scam shield, Verify → Act, Raksha-lite**, Hindi | S1 to S5 |

## 4. Social angles

**In scope** (reuse the engine; each adds a front-door intent or a dashboard view):

| # | Angle | Users | What they get |
|---|---|---|---|
| S1 | **Verify → Act** | Anyone who received or fell for a false forward | Verdict, then the right next step (report to platform, **1930** for money lost, **cybercrime.gov.in** category) and an **evidence pack** backed by a ledger entry. We prepare; the citizen files |
| S2 | **Scam shield** | Elderly and first-time users | Lookalike-link check against real government domains, with the real domain shown; scam forwards routed into S1 |
| S3 | **Disaster rumours** | Districts, control rooms | IMD as official text; disaster topics get priority in the fast clock; the officials' view shows them first |
| S4 | **"Is this notice real?"** | Students, parents, scheme users | Screenshot of a circular or notice, checked against official text, explained in plain Hindi |
| S5 | **Raksha-lite** | People facing morphed or intimate-image abuse, harassment | Guidance only: first steps, what to note, what not to do, StopNCII (hashing on the victim's own device), the cybercrime.gov.in reporting route, helplines. **No uploads, nothing stored, nothing filed** |
| S6 | **Health myths** | Families, health workers | Already works through the Fact Check registry; chosen for the demo |

**Roadmap only** (one slide, with reasons): investment-tip scams with a SEBI register check (needs a probe), communal and
lynching rumours (human review partnership; never auto-labelled, Bible D3), election myths, group guardian bot (privacy design),
newsroom desk with ClaimReview export, WhatsApp intake, full Raksha (Bible D52), face-swap video detection.

**Guardrails** (each is a test or a config rule, not a promise):
- Communal and political content leans to `UNVERIFIABLE_YET` and human review.
- The bot never accepts an image in the Raksha flow and never stores message content beyond the verdict cache.
- Never auto-file to any portal (D42). Every helpline, link and portal route comes from `config/` with its source URL and a
  checked-on date.
- A detector finding nothing is `INCONCLUSIVE`, never "authentic".

## 5. Work packages

**Tier**: Core (the demo needs it), Social (in scope, cut by the checkpoints), Stretch (only if ahead). **Files**: *new* means a
file nobody else edits; *shared* means the integrator applies the change.

### Integrator (person 1, friend's machine: GPU, keys, live runs)

| ID | Package | Tier | h | Files | Done when |
|---|---|---|---|---|---|
| P0 | Merge `fix/review-ledger-schema-tickets`; replace `PLAN_TO_DEADLINE.md` with these two files; restore Bible revision 3 (D56 to D67) from the stash | Core | 0.5 | docs, merge | Branch merged, suite and guard green on this machine |
| P1 | Smaug alone gives `SYNTHETIC_SUSPECTED` evidence, not the `SYNTHETIC` label; media results go through the `Verdict` contract; Smaug licence in `docs/licences.md`; test 5 official satellite or weather images for false alarms | Core | 1.5 | shared: `media.py`, `video.py`, `docs/licences.md` | Unit tests; the satellite result is written in `docs/calibration/` |
| I1 | Finish origin and spread on top of `7f6036b`: **officials' view** that lists spreading clusters with verdict state, disaster topics first (S3); persist members only if time allows | Core | 2 | `fastclock/trace.py`; shared UI | The officials' view lists live clusters with verdict state, disaster topics on top |
| I2 | Reliability: `scripts/preflight.py` (key names, Gemini, Fact Check, Wikidata, PIB, TruFor, ffmpeg, index), `scripts/start_demo.ps1`, Gemini one retry on 5xx or timeout plus fallback to `models[1]`, outage tests, UI "waiting for quota" message | Core | 3 | new scripts; shared `llm/gemini.py` | Preflight green; killing Fact Check gives "source unreachable" in the UI |
| I3 | Wiring: mount A, B and C modules into `api/app.py`, `ui/index.html`, config; one integration test per module | Core | 3 | shared | Each new module reachable from the API and the UI |

### Lane A: claims and official text (person 2 or a Claude session)

| ID | Package | Tier | h | Files | Done when |
|---|---|---|---|---|---|
| A1 | Official-text adapters: **PIB** (RSS or release pages with the descriptive User-Agent) and **IMD**; passages with fetch time and Tier 1; SQLite FTS5 index; `allowed_domains` enforced on every fetch | Core | 3 | new `sources/official.py`, `sources/index.py` | `probe_sources.py` shows both reachable, or the replacement is recorded; a query returns today's passages |
| A2 | **Contrast refutation**: Gemini tags slots (amount, date, count, name); the Tier 1 value must be a span copied from the passage; differing value gives `contradicts` with route `contrast` | Core | 3 | new `claims/contrast.py` | A changed amount fires; equal amount, missing slot and invented span do not |
| A3 | Small NLI model as a second check inside A2 (DeBERTa-class, licence checked) | Stretch | 2 | new `claims/nli.py` | Contrast fires only when NLI also says contradiction; rate recorded |
| S2 | Lookalike-link check: URLs in a post compared with the official-domain list in config (edit distance, homoglyphs, subdomain tricks) | Social | 2 | new `scam/links.py`; `config/official_domains.yaml` | A lookalike is flagged with the real domain; real domains pass |
| S4 | Notice checker: OCR (Gemini vision) → claim → A1 search → plain-language answer | Social | 3 | new `claims/notice.py` | A real circular matches its official page; a fake one returns `UNVERIFIABLE_YET` or `REFUTED` with the reason |

### Lane B: voice and media (person 3 or a Claude session)

| ID | Package | Tier | h | Files | Done when |
|---|---|---|---|---|---|
| B1 | **Voice notes**: faster-whisper transcribes, language kept, text goes into the claim agent | Core | 2 | new `voice/transcribe.py` | A Hindi voice note returns its claims and a verdict |
| B2 | Audio deepfake check, **experimental**: quality gate (duration, clipping, bandwidth) plus one AASIST-class model; abstains on poor audio | Stretch | 4 | new `forensics/audio.py` | Gate tests abstain with reasons; scores on our own real and TTS clips recorded, small sample stated |
| E1 | Evaluation: fact-checked claims through the agent with fact-check lookup off (refute-without-fact-check rate, false-refute rate), per language; TruFor and Smaug numbers; all in `docs/claim-sheet.md` with commands | Core | 3 | new `scripts/eval_claims.py`, docs | Every number on a slide is in the claim sheet with its command |

### Lane C: citizen channels (person 2 or 3, or a Claude session)

| ID | Package | Tier | h | Files | Done when |
|---|---|---|---|---|---|
| C1 | **Telegram bot**: text, photo, voice, video forwarded to the API; reply in the user's language; four intents; per-user rate limit and Gemini quota guard | Core | 3 | new `bots/telegram.py` | A forward of each kind gets a reply; the 6th message in a minute gets a polite wait |
| C2 | **Correction card**: PNG and text, headline, one sentence, official link, no original media, medium or high confidence only; Devanagari font from an OFL font outside `src/` | Core | 2 | new `cards/render.py`, `assets/fonts/` | A refuted item yields a card in Hindi and English |
| S1 | **Verify → Act**: next-step routes in `config/actions.yaml` (source URL, checked date); evidence pack (forward text, URLs, times, verdict, sources, ledger seq and hash) as a text file the user can save | Core | 4 | new `act/` | A scam forward returns the 1930 route and an evidence pack whose ledger entry verifies |
| S5 | **Raksha-lite** in the bot: menu-driven guidance, no uploads accepted in this flow | Social | 2 code + 2 content | new `bots/raksha.py`, `config/raksha.yaml` | The flow runs end to end; sending an image in it is refused with a kind message |
| E2 | README: what is real vs roadmap, what each model catches and misses, licences and credits (TruFor GRIP-UNINA, Smaug Abacus.AI), how to run | Core | 2 | `README.md` | A teammate runs the demo from it |

### Shared

| ID | Package | Tier | h | Done when |
|---|---|---|---|---|
| D1 | Demo items (real, circulating, already fact-checked, two languages), pitch deck, two cold-start rehearsals | Core | 3 per person | G-final in `BUILD_PLAN.md` passes |
| X1 | Spoken Hindi reply for voice-note users | Stretch | 2 to 3 | |

**Totals.** Core 32 h (integrator 10, lane A 6, lane B 5, lane C 11), Social 9 h (S2, S4, S5), Stretch 8 to 9 h (A3, B2, X1),
plus D1 at 3 h per person. About 25 productive hours per person remain, so Core and Social fit with Claude sessions running in
parallel; Stretch is taken only after checkpoint CP2 passes with time to spare. S6 (health) needs no build, only demo items.

**India fit (IN1 to IN7, about 11 h):** languages measured, Indian scam types, more official text, regional-language fact-check search, a sensitive-topic guard,
voice on Indian speech and seasonal topics. Packages, order, cuts and the say or do-not-say list are in `BUILD_PLAN.md`, section "India fit".

**Throughput and the funnel (TH1a to TH1d, about 3.7 h):** the benchmark that turns the "high-throughput" clause into measured numbers, and the Radar, Verifier, Responder
framing for the pitch. Packages, the five slide numbers and the say or do-not-say rules are in `BUILD_PLAN.md`, section "Throughput and the funnel".

## 6. File ownership (avoids merge conflicts)

| Owner | Files |
|---|---|
| Integrator only | `src/satyasutra/api/app.py`, `ui/index.html`, `src/satyasutra/media.py`, `src/satyasutra/video.py`, `src/satyasutra/llm/gemini.py`, `config/*.yaml` (others send the YAML block in their PR description), `forensics/service.py`, `CLAUDE.md` |
| Lane A | `src/satyasutra/sources/official.py`, `sources/index.py`, `claims/contrast.py`, `claims/nli.py`, `claims/notice.py`, `scam/` |
| Lane B | `src/satyasutra/voice/`, `forensics/audio.py`, `scripts/eval_claims.py`, `docs/claim-sheet.md` |
| Lane C | `src/satyasutra/bots/`, `cards/`, `act/`, `assets/fonts/`, `README.md` |

Rules: one branch per package from `phase-8/prototype-ui`; push and tell the integrator; the integrator merges at the merge windows
in `BUILD_PLAN.md`; nobody edits another owner's files without saying so first. Each Claude session gets this file and its
package ID as its brief.

## 7. Approvals only the user can give

| Item | Why | Needed by |
|---|---|---|
| Fresh Telegram token in `.env` (typed by the user, never pasted in chat) | The old token was exposed | C1 start |
| faster-whisper model download | B1 | B1 start |
| Devanagari font download (Noto Sans Devanagari, OFL) | C2 | C2 start |
| AASIST-class audio model download and its licence | B2 | B2 start |
| Small NLI model download and its licence | A3 | A3 start |
| Confirm the Smaug weights download was approved | Already in use | now |
| MiniCheck from a git branch | Roadmap; not needed for this plan | |

## 8. Robustness requirements

The prototype is "solid" when every line below has passed on the demo machine.

| Requirement | Check |
|---|---|
| Starts from cold with one command | `scripts/start_demo.ps1`, then `scripts/preflight.py` all green |
| No silent failure | Each source outage shows "source unreachable" and the verdict names it (I2 tests) |
| Survives Gemini limits | 15 requests/min spacing, one retry, fallback model, quota guard in the bot, verdict cache for repeats |
| Survives judges on their phones | Per-user rate limit; the bot answers "busy, try in a minute" rather than timing out |
| Never overclaims | Contract validators on both axes; `SYNTHETIC_SUSPECTED` not `SYNTHETIC` from one detector; no "authentic" from no-hit |
| Tamper-evident | `scripts/verify_ledger.py` (read-only) prints "chain intact" during the demo |
| Secrets safe | Keys only in `.env`; errors report exception class names; token re-issued |
| Tests and guard | Full suite, `check_no_fixtures.py` and `forge.py check` green on `main` after the final merge; CI green |
| Backup | Screenshots of a good run kept, shown only if the network dies, and said to be screenshots |

## 9. Standing caveats (say these in the demo)

TruFor misses most edits after video compression. Smaug was measured on 80 Commons images and flagged one satellite image.
Audio is experimental. Hindi beyond a few claims is unproven. Spread numbers describe Bluesky only. A no-hit is never "authentic".
Escalation has not fired on a real item. Thresholds come from small sets, each written in `config/thresholds.yaml`.
