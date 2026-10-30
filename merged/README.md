<div align="center">

# SATYASUTRA (सत्यसूत्र)

**Where did it come from, what does the evidence say, and what can we honestly not tell**

*A multilingual claim-verification, provenance and media-audit engine for Indian civic misinformation, grounded in official sources and live feeds, with an evidence ledger that can be re-checked*

**Hackathon track: Theme 1, Civic Pulse, Digital Governance & Public Information · Team: _fill in_**

</div>

---

> **Status of this document.** This is the merged build README, written *before* the code. Every feature starts as ⬜ planned.
> A row changes to ✅ only when its "done when" command passes. Nothing here is a claim until the [claim sheet](#claim-sheet)
> has a number and a command for it. **SATYASUTRA is a working name for the merged plan**; change it freely. Names marked
> _fill in_ and numbers marked _measure_ are placeholders on purpose. All hour figures in the plan are estimates, and the plan
> assumes a 48-hour event and a team of four, which are assumptions to confirm.

**Plan documents (all in `docs/`):** [Bible](docs/BIBLE.md) (design, rules, threat model, decisions D1 to D46, licence register) ·
[master build map](docs/BUILD_MAP.md) (lanes, schedule, gates, cut ladder, demo plan) ·
[claims build map](docs/BUILD_MAP_CLAIMS.md) · [provenance build map](docs/BUILD_MAP_PROVENANCE.md) ·
[forensics build map](docs/BUILD_MAP_FORENSICS.md) · [Raksha build map](docs/BUILD_MAP_RAKSHA.md)

## Overview

Most checkers end in one score: "78% fake". Nobody can look inside a score, and it is wrong in ways that hurt, especially for
political or religious content. This system keeps two separate questions apart and answers each with evidence:

- **Is the claim supported?** Checked against official Indian primary sources and published fact-checks.
- **Is the media what it is said to be?** Checked by where it came from first, then by forensic detectors, with the file's
  quality tested before any detector is trusted.

It says "we cannot tell yet" whenever that is true, and every answer goes into an append-only ledger that can be re-checked.

It works in two clocks. A **fast clock** (plain code, no language model) ingests live posts, clusters near-duplicates and
ranks them by spread. An **event clock** (a LangGraph agent behind a policy gate) investigates only what the fast clock
escalates, or what a person submits.

For every item it asks, in order:

1. Have we already checked this item or a near-copy? (cluster and cache)
2. Where did it come from, and is there an older copy? (provenance)
3. Is there a checkable claim in it, in any Indian language, or in an image of text? (claim extraction)
4. What do official sources and fact-checks say? (retrieval with citation)
5. If it is audio or an image, does it show signs of synthesis or editing, and is the file good enough to tell? (forensics)
6. Do the signals agree, and is there enough to say anything? (fusion)
7. Who spread it first, and who amplified it? (variant graph)

## Verdicts: two axes, never mixed

A post can carry a true claim with a fake image, or a real image with a false caption. So the two axes are separate.

**Claim axis**

| Label | Meaning | Rule |
|---|---|---|
| `SUPPORTED` | A trusted source backs the claim | A Tier 1 or Tier 2 passage (see below) is entailed by the claim's wording, or a trusted fact-check rated it true |
| `REFUTED` | A trusted source contradicts the claim | A trusted fact-check rated the matching claim false, or an official passage states the opposite. "Not supported" alone is never enough |
| `MISSING_CONTEXT` | Literally true but misleading, or only part of the story | The verifier supports the core and a retrieved passage adds a material qualifier. Shown with both passages |
| `UNVERIFIABLE_YET` | We cannot tell, possibly only for now | **Default.** No evidence, or only Tier 3 and Tier 4 sources. Re-checked later on a schedule |
| `NOT_A_CLAIM` | Opinion, question or satire with no checkable assertion | Claim extraction found nothing checkable |

**Media axis**

| Label | Meaning | Rule |
|---|---|---|
| `OUT_OF_CONTEXT` | Real media, reused with a different date, place or story | Provenance found an earlier appearance with a trustworthy date, or a matching page naming a different place |
| `EDITED` | A region appears altered | Localisation above threshold and reliability layer passed, or a credential says edited |
| `SYNTHETIC` | Signs of AI generation | Calibrated detector(s) above the high cutoff with the quality gate passed and no conflict, or a credential says AI-generated |
| `AUTHENTIC_IN_CONTEXT` | Consistent with where and when it is said to be from | Valid signed credential, or a consistent earliest-copy match with detectors that reliably find nothing. **No earlier hit alone never produces this** |
| `INCONCLUSIVE` | We cannot tell | **Default.** Gate failed, detectors disagree, or no usable evidence |

**Source-tier rule.** Sources are grouped: Tier 1 official primary (government, regulators, courts), Tier 2 accountable fact-checkers
and wire services, Tier 3 reputable news, Tier 4 everything else. `SUPPORTED` and `REFUTED` require Tier 1 or Tier 2.
Tier 3 and Tier 4 evidence is shown as context and can only produce `UNVERIFIABLE_YET`. The tier list lives in config, not code.

Both unknown labels are results, not failures. The schema forces them when evidence is empty, so "no evidence" can never become a
confident answer. Confidence is shown as `low`, `medium` or `high`, never as a made-up percentage.

## Features

| Capability | Description | Status |
|---|---|---|
| Live ingestion | Public firehose and news feeds through one `Source` interface; no replay mode exists | ⬜ |
| Fast clock | Plain-code normalise, MinHash text clusters, media fingerprints, velocity ranking; no model call | ⬜ |
| Cluster before inference | One audit per cluster, fanned out to all copies | ⬜ |
| Indian-language claims | Detect language, normalise Hinglish and Indic scripts, extract atomic claims, quote the original back | ⬜ |
| Screenshot claims | Read text from images (OCR or vision model) and treat it as the claim | ⬜ |
| Primary-source retrieval | Official adapters plus ClaimReview lookup, BM25 plus dense retrieval, source tiers; every verdict cites a fetched passage | ⬜ |
| Evidence checking | MiniCheck-style verifier returns support probability and the exact chunk; threshold calibrated on our own data. See [claims map](docs/BUILD_MAP_CLAIMS.md) | ⬜ |
| Guarded justification | Each sentence of an explanation must itself be supported by the passage it cites, or it is dropped | ⬜ |
| Verification agent | LangGraph state graph: plan, parallel tool calls, fuse, draft, verify, retry, abstain; behind a policy gate; live trace in the UI | ⬜ |
| Policy gate | Allowlist, per-claim time and tool budget, read-only; denials logged and shown | ⬜ |
| **Reverse-image provenance** | Fingerprints, C2PA and EXIF, reverse image search for an earlier appearance; compares dates and places to flag `OUT_OF_CONTEXT`. See [provenance map](docs/BUILD_MAP_PROVENANCE.md) | ⬜ |
| **Variant graph** | Earliest item we saw, word-level diff between wording variants ("50" became "500"), top amplifiers, phrased as "coordinated-looking" | ⬜ |
| Audio forensics | Two pretrained anti-spoofing detectors (ONNX) behind a quality gate, calibrated per codec condition, disagreement abstains; segment timeline. See [audio track](docs/BUILD_MAP_FORENSICS.md) | ⬜ |
| Codec robustness matrix | Every audio result reported for clean, Opus, narrowband, MP3, AAC and two-hop forwarded audio | ⬜ |
| Edit localisation (Tier 2) | TruFor map, integrity score and reliability layer as an overlay | ⬜ |
| Image classifiers (Tier 2) | Pretrained general and face-specific classifiers behind a gate | ⬜ |
| Video pulse forensics (Tier 2) | Pulse from face regions with a quality gate; supporting evidence only, never proof | ⬜ |
| Fusion with abstention | Per-axis rules; conflict is reported, not averaged; provenance outranks detector scores | ⬜ |
| Evidence ledger | Append-only, hash-chained, idempotent, re-checkable | ⬜ |
| Telegram bot | Forward in, verdict card out, reply in the user's language (WhatsApp is Tier 3) | ⬜ |
| Reviewer queue | Ranked by spread and uncertainty; every decision can be undone (Tier 2) | ⬜ |
| **Raksha: intimate-image abuse support** | For a person whose private images are leaked or threatened: images are fingerprinted **in their browser and never uploaded**, an evidence pack is encrypted with a key only they hold, and takedown and police-complaint text is prepared in their language with a case checklist and helplines. Operators cannot view the images. We prepare and track; the person files. See [Raksha map](docs/BUILD_MAP_RAKSHA.md) | ⬜ |
| Scam alerts (Tier 2) | Voice-clone call check for families, and lookalike government-scheme link check | ⬜ |

---

## A judge in three minutes

| Minute | Do this | What you will see |
|---|---|---|
| 1 | Open the dashboard, read the **Sources** panel and live clusters | Each live source with its last fetch time; clusters ranked by how fast they spread |
| 1 | Forward an image post to the Telegram bot, or paste it in the web box | The claim read from the image, the live agent trace, the earlier appearance of the picture with a link and date, and a reply in the sender's language |
| 2 | Upload your own voice note or image | A forensic result with its quality gate, a segment timeline for audio, or an honest `INCONCLUSIVE` if the file is too degraded |
| 2 | Open a live cluster's provenance view | The earliest item we saw, wording variants with a word-level diff, and top amplifiers |
| 3 | Try the robustness drills, then **Denied actions**, **Reconcile** and the ledger verify | Injection text ignored, a dead source named, the gate refusing an out-of-scope source |

Not here, so you need not look: measured accuracy on real attacks until the claim sheet says so, WhatsApp or X content, and
legal conclusions. See [Limits](#limits).

---

## The problem

India's misinformation travels mostly through closed channels, in many languages, as compressed forwards, voice notes and
screenshots of text. Four gaps make existing tools weaker than they look:

1. **Verdicts without sources.** A score cannot be audited, and a language model can state a false "fact" fluently.
2. **English-first.** Claims in Hindi, Tamil, Bengali or Hinglish are often never checked.
3. **Compressed media defeats pixel forensics.** A forwarded video or voice note has lost most of the signal a detector needs.
4. **Real media, wrong story.** Old or foreign pictures reused for a new event need no AI at all, and are probably common (an
   assumption from our research, not yet measured by us). Provenance, not a detector, is the answer.

| Requirement | What this system does |
|---|---|
| Real-time, live data | Ingestion reads live endpoints only; the health panel shows fetch times |
| Fact verification | Retrieval against official sources; citation required for every decisive claim verdict |
| Deepfake audit | Audio and image detectors with quality gates and abstention |
| Provenance tracing | Reverse-image search, fingerprint clusters, variant graph with earliest item and amplifiers |
| Source attribution, few hallucinations | Verdict schema rejects an answer with no evidence record; justifications re-verified |
| Edge-case resilience | Typed errors and a tested degraded mode for each dependency |

---

## Architecture

```
 FAST CLOCK  (plain code, no language model)
 live feeds ─► INGEST ─► NORMALISE ─► CLUSTER (text and media) ─► VELOCITY RANK ─┐
                                              │                                    │ escalate
 Telegram / web box / uploads ────────────────┴────────────────────────────────────┤
                                                                                    ▼
 EVENT CLOCK (LangGraph agent behind the policy gate; streams a live trace)
   PLAN ─► parallel tools ─► FUSE ─► DRAFT ─► VERIFY ─► RETRY or ABSTAIN
            ├ claims: translate, OCR, extract, retrieve, verify
            ├ provenance: fingerprints, C2PA, reverse search, variant graph
            └ forensics: audio gate and detectors (image and video are Tier 2)
                                                                                    ▼
                         FUSION (two axes, abstain) ─► LEDGER ─► API / UI / BOT
```

| Module | Does | Does not |
|---|---|---|
| `ingest` | Pulls from configured sources, normalises to one `Post` type | Contain any URL or key |
| `fastclock` / `cluster` | Hash-based dedupe, verdict cache, velocity score | Run any model beyond hashing |
| `claims` | Extraction, retrieval, entailment, verdict rules | Answer without a retrieved passage |
| `agent` | LangGraph planning, tool calls, retries, abstaining | Hold write access or bypass the gate |
| `gate` | Allowlist, budgets (including reverse-search budget), denial log | Hold write access to anything |
| `provenance` | Fingerprints, credentials, reverse search, out-of-context rule | Treat "no hit" as "authentic" |
| `graph` | Variants, earliest item, amplifiers, mutation diff | Name a person or claim intent |
| `forensics` | Run detectors and report their own quality gate | Return a label when the gate fails |
| `fusion` | Combine signals per axis, report conflict, abstain | Hide a disagreement |
| `ledger` | Append, hash-chain, re-verify | Allow update or delete |
| `bots`, `api`, `ui` | Serve verdicts and evidence | Compute verdicts |
| `raksha` | Hash-only case store, takedown and complaint text, hard stops | Receive images, passphrases or notes; log in to portals for anyone |

## Repository layout

```
satyasutra/
├── config/
│   ├── sources.yaml        # every live source: base URL, auth env var, rate limit, cache TTL, trust tier
│   ├── policy.yaml         # gate allowlist and budgets
│   └── thresholds.yaml     # detector thresholds, bound to the model version they were set for
├── src/satyasutra/
│   ├── ingest/  fastclock/  cluster/  claims/  agent/  gate/
│   ├── provenance/  graph/  forensics/  fusion/  ledger/  bots/  api/  raksha/
├── ui/                     # ui/raksha/ holds the in-browser hashing and encryption code
│   └── raksha/
├── scripts/
│   ├── probe_sources.py    # run first: which sources are reachable from this machine
│   ├── verify_ledger.py    # re-walk the hash chain
│   └── check_no_fixtures.py
├── tests/
│   ├── unit/               # recorded responses are allowed HERE and only here
│   ├── contract/           # hit live sources, marked slow; run before every demo
│   ├── e2e/                # live end-to-end cases
│   └── drills/             # failure scenarios, see Edge cases
├── docs/
│   ├── BIBLE.md  BUILD_MAP*.md
│   ├── modules/            # one page per module, written by its owner
│   └── claim-sheet.md
└── README.md
```

---

## No hardcoding, enforced

The hackathon rules disqualify static or mock data. These are checks that fail the build, not promises:

| Rule | How it is enforced |
|---|---|
| No URL, key or threshold in code | `check_no_fixtures.py` scans `src/` for URLs, data files and numeric literals in decision paths; CI fails on a hit |
| Fixtures stay in tests | An import test fails if anything under `src/` imports from `tests/` or reads `tests/fixtures` |
| No demo mode | No flag swaps a live source for canned data; a test greps for common names |
| No fabricated verdicts | `Verdict` requires `evidence`; an empty list is coerced to `UNVERIFIABLE_YET` or `INCONCLUSIVE` in the constructor |
| Evidence is fresh and real | Each `Evidence` carries `source_id`, `url`, `fetched_at`, `passage_sha256`; the UI shows the fetch age |
| A dead source is visible | Adapters raise typed errors (`Unreachable`, `RateLimited`, `SchemaChanged`); the agent records them and the UI shows them |
| Contract tests stay honest | `tests/contract` call live endpoints; a failure shows in the Sources panel before the demo does |
| The demo uses real items | Chosen at rehearsal from circulating, already-fact-checked content; nothing staged or fabricated |

Recorded API responses in `tests/unit` are fine for fast tests. They never appear on the runtime path.

## Live sources

Access is **unverified** until `scripts/probe_sources.py` has run on the demo machine. Fill the status column from its output.

| Role | Candidate | Auth | Status |
|---|---|---|---|
| Public feed | Bluesky Jetstream | none | ⬜ probe |
| Public feed (Tier 2) | Reddit, YouTube metadata, public Telegram channels, GDELT | varies | ⬜ probe |
| News | RSS from national and regional outlets | none | ⬜ probe |
| Fact-checks | Google Fact Check Tools (ClaimReview) | API key | ⬜ probe |
| Official | PIB, Data.gov.in, India Code, eGazette, ECI | varies | ⬜ probe |
| Weather and disaster | IMD, NDMA | varies | ⬜ probe |
| Geospatial | ISRO Bhuvan | registration | ⬜ probe |
| Reverse image search | Google Cloud Vision web detection | API key, paid beyond allowance | ⬜ probe |
| Link archive | Internet Archive CDX | none | ⬜ probe |
| Language | Bhashini, AI4Bharat models | registration, local | ⬜ probe |
| Entities | Wikidata, Wikipedia | none | ⬜ probe |

If a source turns out to be unavailable, the adapter is removed from `sources.yaml`, the claim sheet notes it, and the demo says so.

---

## Build journey

Each phase ends in something you can run. Do not start a phase until the previous "done when" passes.

| # | Phase | Build | Done when |
|---|---|---|---|
| 0 | Skeleton | Repo, config loader, `/health`, `probe_sources.py`, CI with the no-fixtures check, contracts | `probe_sources.py` prints a real reachability table and CI is green |
| 1 | Walking skeleton | One live post → one claim → one ClaimReview lookup → verdict with citation → ledger row | `curl /verify` on a live post returns a cited verdict and `verify_ledger.py` passes |
| 2 | Fast clock | Source interface, queue, MinHash and media fingerprint clusters, verdict cache, velocity rank | The same text twice returns the cached verdict and runs no second audit |
| 3 | Claims and agent | [`BUILD_MAP_CLAIMS.md`](docs/BUILD_MAP_CLAIMS.md), C0 to C10: verifier, calibrated threshold, sources and index, retrieval, extraction (with OCR), tier-aware rules, guarded justification, LangGraph agent and gate | Killing a source mid-run records an error and a different source is tried; `eval_claims.py` fills the claim sheet |
| 4 | Provenance | [`BUILD_MAP_PROVENANCE.md`](docs/BUILD_MAP_PROVENANCE.md), PV-1 to PV-4: intake, fingerprints, reverse search, out-of-context rule, variant graph | A known recirculated image returns an earlier appearance with date and URL; a live cluster shows its earliest item and a word-level diff |
| 5 | Indian languages | Language detection, normalisation, translate-and-quote-back, reply in the user's language | A Hindi claim returns a verdict and the user's original text |
| 6 | Audio forensics | [`BUILD_MAP_FORENSICS.md`](docs/BUILD_MAP_FORENSICS.md), audio track A0 to A9 | `calibrate_quality_gate.py` regenerates thresholds; a degraded file returns `INCONCLUSIVE` with a reason; `eval_forensics.py` fills the claim sheet by codec condition |
| 7 | Fusion and ledger | Per-axis rules, conflict reporting, provenance-over-detector ordering, hash chain | Editing a ledger row by hand makes `verify_ledger.py` fail |
| 8 | Bot and UI | Telegram bot, live trace, provenance view, audio timeline, denied actions | A forward from a phone returns a verdict card within the target time |
| 9 | Hardening | All drills, UI states, rehearsal | Every drill passes and the demo path runs twice with no edits |
| 8b | Raksha | [`BUILD_MAP_RAKSHA.md`](docs/BUILD_MAP_RAKSHA.md), RK-1 to RK-5, built by its own lane in parallel from hour 0 | The network-interception test finds no image bytes, passphrase or notes in any request; each hard stop halts the flow; deleting a case returns a receipt |
| 10 | Tier 2 (only if green) | Image forensics and TruFor first, then the gated video pulse path, extra feeds, reviewer queue | Each Tier 2 item has its own claim-sheet row or is dropped |

**If time runs short** follow the [cut ladder](docs/BUILD_MAP.md#4-cut-ladder) in the master map. Never cut the walking skeleton,
the gate, the ledger, the audio quality gate, reverse-image provenance or the drills. They are what the rubric scores.

## Edge cases and drills

Each is a test in `tests/drills`, and each ends in a visible, non-crashing state.

| Scenario | Expected behaviour |
|---|---|
| Live feed returns nothing | UI says "no new posts since _time_", health stays green |
| A source is rate-limited or down | Typed error recorded, another allowed source tried, result notes the gap |
| Source changes its schema | `SchemaChanged` raised, adapter disabled, other sources unaffected |
| Claim has no evidence, or a brand-new claim | `UNVERIFIABLE_YET`, shown with the sources searched, re-checked later |
| Only low-tier sources found | `UNVERIFIABLE_YET`, the sources shown as context |
| Two sources contradict | Both shown, conflict flagged, no averaged verdict |
| Image with an earlier appearance | `OUT_OF_CONTEXT` with the earlier link, date and snippet |
| Image with no earlier appearance | No provenance verdict; never `AUTHENTIC_IN_CONTEXT` on that alone |
| Reverse-search quota exhausted or no key | Internal provenance only, flag `PROVENANCE_LIMITED`, said on screen |
| Corrupt, huge or wrong-type upload | Rejected with a reason, no crash, no partial ledger row |
| Media too compressed to analyse | Detectors abstain, reason shown |
| Detectors disagree | Flag `CONFLICT`, no averaged label |
| Prompt injection inside a post | Text is data; the instruction is ignored; the gate is unchanged |
| Agent exceeds budget or asks for a blocked source | Gate denies, denial logged and visible |
| Same item submitted twice | Idempotent: same ledger entry, no duplicate work |
| Network drops during a demo | Cached verdicts still readable, new checks show "offline", recovery is automatic |

---

## Claim sheet

Every number in the pitch comes from this table. A claim with no measured value is not made.

| Claim | Value | Reproduce | Not claimed |
|---|---|---|---|
| Cluster cache avoids repeat audits | _measure_ | `scripts/bench_cluster.py` | Behaviour on adversarially edited media |
| Verdicts with no evidence are impossible | tested | `pytest tests/unit/test_verdict.py` | Evidence being correct |
| Ledger tampering is detected | tested | `scripts/verify_ledger.py` | Protection against a malicious database owner |
| Claim-verdict agreement with human fact-checks | _measure_ | `scripts/eval_claims.py` | Coverage of all topics or languages |
| Recall of earlier appearance for recirculated images | _measure_ | `scripts/eval_provenance.py` | Images that exist only in closed groups |
| False `OUT_OF_CONTEXT` rate on fresh original photos | _measure_ | `scripts/eval_provenance.py` | Every kind of new photo |
| Fingerprint match rate by edit condition | _measure_ | `scripts/eval_fingerprints.py` | Adversarial edits |
| Audio detector error by codec condition | _measure_ | `scripts/eval_forensics.py` | Real-world attack rates |
| Error by language | _measure_ | `scripts/eval_slices.py` | Parity across languages |
| End-to-end latency by tier | _measure_ | `scripts/bench_latency.py` | Performance under load |
| Raksha server never receives media, passphrase or notes | tested | `pytest tests/e2e/test_raksha_network.py` | Safety against a compromised device or network observer |
| Raksha cases expire and delete fully | tested | `pytest tests/unit/test_raksha_store.py` | Removal of content from platforms |
| Hash match rate by edit condition (benign teammate photos only) | _measure_ | `scripts/eval_fingerprints.py` | Intimate content, or images we may not test with |

Evaluation sets may be public benchmark files or live fact-check feeds, used offline to measure the system with leakage checks.
The demo path stays live.

## Ownership

Every member must explain the project alone. Each lane owner writes `docs/modules/<name>.md` (what it does, inputs, outputs, one
failure and its recovery) and reads every other page. The ten questions each person must answer are in the
[master map](docs/BUILD_MAP.md#9-questions-every-member-must-answer-alone).

| Lane | Modules | Owner | Reviewer |
|---|---|---|---|
| 1 Platform, data, graph | ingest, fastclock, cluster, graph | _fill in_ | _fill in_ |
| 2 Claims and agent | claims, agent, gate | _fill in_ | _fill in_ |
| 3 Forensics, provenance, bot | forensics, provenance, bots | _fill in_ | _fill in_ |
| 4 Core and UI | ledger, fusion, api, ui | _fill in_ | _fill in_ |
| 5 Raksha | raksha, ui/raksha | _fill in_ | _fill in_ |

Reusing open-source libraries and boilerplate is allowed. Core logic, models and agent workflows are written during the event
window, and nothing is copied from an earlier project.

## Limits

- Closed channels such as WhatsApp cannot be crawled. Content arrives only when a person submits it.
- A verdict is only as good as the sources it retrieved. Official sources can be slow, incomplete or wrong.
- Reverse search finds only what a search service has indexed, costs money beyond a small allowance and may be rate limited. "No
  earlier appearance" means we found none, not that none exists. "Earliest item we saw" is not "origin".
- Detectors degrade on compressed media and on unseen generators. The claim sheet reports this by slice.
- A measured pulse never proves a video real: good face swaps can copy the pulse of the source video. It is a gated Tier 2
  supporting signal.
- The evidence verifier says whether a passage supports a claim, not whether it is false, and its base models are English, so
  Indian-language items are translated first and reported separately. HerO and HerO 2 are design references only.
  Bespoke-MiniCheck-7B is non-commercial.
- Audio detectors are pretrained models we calibrate, gate and combine, not trained from scratch. Their published error rates
  are their authors', not ours. The AntiDeepfake weights are CC BY-NC-SA 4.0. Fake test audio is made only from consenting
  teammates' voices or generic synthetic voices; we never clone a real person.
- Image detectors are pretrained, mostly trained on faces or specific generators, with non-commercial terms; TruFor is limited to
  informational and nonprofit use and runs as a separate container. "AI-generated", "edited" and "misleading" are different
  questions.
- Third-party code is used with care: the rPPG-Toolbox is under the Responsible AI Source Code License and is only an external
  benchmark; the DeepFakesON-Phys repository shows no licence, so its code and weights are not copied.
- Amplifier lists describe coordinated-looking activity from timestamps and shared clusters. They do not prove bots or intent,
  and raw account handles are hashed in the public view.
- Text-generation detectors are unreliable and are never the sole basis for a verdict.
- Political, religious and communal content is routed to human review, and the system errs toward the unknown labels.
- Submitted media may contain personal data. Store hashes and verdicts, not raw submissions, unless retention is declared.
- This is not legal advice and makes no statement of regulatory compliance.
- Raksha prepares takedown and complaint material and tracks a case. It does **not** file complaints automatically (no public
  submission API is known to us, and bypassing logins or CAPTCHAs is not allowed), does not guarantee removal, cannot protect a
  compromised device, and its legal references and helpline list need human sign-off before use. A possible minor stops the flow.
  Anyone can claim to be the person pictured, which we cannot verify without seeing the image, so the person files, not us.
- Time, team size and hours are assumptions. Tier 1 is estimated at 156 hours: about 14 under capacity for five people and 20
  over for four.

## Quick start

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config/sources.example.yaml config/sources.yaml    # add your own keys through environment variables
python scripts/probe_sources.py                        # run this first
uvicorn satyasutra.api:app --reload
pytest -q                                              # unit and no-fixtures checks
pytest -m contract                                     # live-source checks, run before any demo
```
