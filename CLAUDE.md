# FORGE: working notes for an agent picking this up

FORGE is the build repository for **SATYASUTRA**, a hackathon project (Theme 1, Civic Pulse): a real-time fact-verification and
deepfake audit engine for Indian social media. The product code is the Python package `satyasutra` under `src/`.
Remote: `github.com/aadamaftabpersonal-boop/FORGEHackathon` (public).

## Hackathon rules that shape every decision

- **No mock, static or hardcoded data in product code.** Live APIs only. `scripts/check_no_fixtures.py` enforces it on `src/`:
  no URL literals, no data files, no imports from `tests`, no demo/mock/fake names, no numeric literals in comparisons.
  Put values in `config/*.yaml`. A deliberate exception needs `# forge: allow-literal <reason>` on that line.
- Every team member must be able to explain the code, no pre-recorded demos, and core logic is built inside the event window.
- Never auto-file to government portals, defeat CAPTCHAs or bypass logins (plan decision D42). Raksha (the support flow for leaked
  intimate images) is roadmap only, not in this build (D52).
- Do not present unverified facts as verified. Say what was not tested.

## Workflow: phases, tickets, commits

- `docs/PHASES.md` has the phase table and gates. Tickets are markdown files in `tickets/` (`F<phase>-<nn>.md`) with a YAML header,
  a **Done when** section and a **Closed** section written on completion. Tickets for a phase are written when it starts.
- CLI: `python tools/forge.py board | list | show ID | start ID | done ID --evidence "..." | new | check`.
  `start` refuses if a dependency is not done. `done` requires the ticket to be started and records date, evidence and commit.
  Do not put a colon in a ticket title (it breaks the YAML header).
- **Evidence must be true.** Close a ticket only after its Done-when command passes, and say what was not verified.
  Do not pipe test output through `tail`/`head` when you need the exit code; it hides failures.
- One commit per finished chunk, message starts `F<phase>-<nn>:`, branch per phase (`phase-1/walking-skeleton`). Commits end with
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- `main` is at the Phase 0 gate (tag `phase-0-complete`). Merge a phase branch into `main` only when its gate in `PHASES.md` passes.

## Commands (Windows; the venv is `D:\FORGE\.venv`, Python 3.14)

```
.\.venv\Scripts\python -m pytest -q                  # unit tests; live-internet tests are excluded by default
.\.venv\Scripts\python -m pytest -q -m contract      # live contract tests (need .env keys and network)
.\.venv\Scripts\python scripts\check_no_fixtures.py  # the guard
.\.venv\Scripts\python tools\forge.py check          # ticket validity
.\.venv\Scripts\python scripts\probe_sources.py      # live reachability table of every configured source
uvicorn satyasutra.api.app:create_app --factory      # the API (/health, /health/sources, /ledger/verify, /ledger/{seq}, POST /verify)
.\.venv\Scripts\python scripts\verify_live.py --posts 8   # live posts through the whole /verify path
.\.venv\Scripts\python scripts\build_index.py       # fill the debunked-image index from the live feeds
.\.venv\Scripts\python scripts\match_image.py <file-or-url>
```

The Bash tool is Git Bash; the user's own terminal is PowerShell (no `&&`, no `printf`). Heredocs containing apostrophes can break
the Bash tool: use the file Write tool for multi-file edits.

## Running the prototype (branch `phase-8/prototype-ui`, the demo path)

Priority is a working, good-looking demo for the hackathon deadline (2 Nov 00:00), not benchmarks or ticket ceremony. The no-mock-data
rule still applies. Three processes:

```
.\.venv\Scripts\python scripts\serve.py --port 8000                       # API + UI at http://127.0.0.1:8000/ with the live stream worker on
forensics\venv311\Scripts\python forensics\service.py --port 8100          # TruFor on the GPU (Python 3.11 + CUDA torch, git-ignored)
.\.venv\Scripts\python scripts\media_live.py --base http://127.0.0.1:8000  # smoke test of the media path on live Commons photos
```

- `ui/index.html` is one static page: claim box (streams `GET /agent/stream?text=` and shows each node and tool call live, then the
  verdict card with Wikidata entities), image panel (`POST /media/check`, raw bytes, heatmap overlay), live top clusters, ledger check.
- `agent/graph.py` is the real LangGraph claim agent: read_post, extract, then registry and ground in parallel, then judge, then
  decide (rules + ledger). It stops early when there is no checkable claim. `tool_calls` needs an additive reducer because two nodes
  run in parallel.
- `claims/judge.py`: Gemini only says whether a fact-check review is the same, opposite or a different claim; the rules still map the
  rating. Failure falls back to the strict word-overlap rule. First live REFUTED seen: "COVID-19 vaccines contain microchips" via a
  FactCheck.org review. `claims/entities.py` + `sources/wikidata.py` ground named entities to Wikidata (context only, never evidence).
- `media.py`: provenance matches (debunked-image index, 117 items) and TruFor are separate signals. Label EDITED at or above
  `trufor_manipulation_score` (0.7, calibrated on 30 Commons photos with self-made splices: see `docs/calibration/trufor_2026-10-31.txt`
  and the note in `config/thresholds.yaml`), otherwise INCONCLUSIVE, never "authentic". TruFor does not detect AI-generated images.
- `forensics/service.py` is ours; `forensics/trufor/` (clone of grip-unina/TruFor), its weights and `venv311` are git-ignored.
  TruFor licence: informational and nonprofit use only, credit the authors (GRIP-UNINA, University Federico II of Naples).
  The checkpoint is loaded with `weights_only=True` plus a narrow allowlist after reading its pickle statically (only tensors and a numpy scalar).
- Starting a second server on a port a stale one holds fails silently with exit 3 and the old code keeps answering: find the PID with
  `ps -W | grep FORGE/.venv` and `kill` that one only. Never kill all python processes (a pip install or the TruFor service may be running).
- Hindi works through the same agent (Gemini extracts, translates to English, keeps the Hindi quote; the Fact Check API finds nothing for
  Hindi query text, 0 hits on 4 claims, so the English rendering is what is searched). `tests/contract/test_live_hindi.py`.
- Video: `video.py` + `POST /video/check` sample 8 frames with ffmpeg (already on PATH, no extra install), score each with TruFor and
  the debunked-image index. Measured weakness: a spliced frame scored 0.996 as a still and 0.27 after H.264 (`scripts/video_live.py`),
  so a clean video result is weak evidence and the result text says so. Face-swap and AI-generated video are not detected.
- Voice notes: `voice/transcribe.py` + `POST /voice/transcribe` (raw audio, max 120 s). ffmpeg decodes to 16 kHz mono; the GPU service
  (`forensics/service.py` `/transcribe`, faster-whisper small from `models/whisper-small`, git-ignored, 486 MB) answers first, local CPU is
  the fallback (about 1.7x slower than real time). The UI transcribes, shows the text and language, then runs the normal claim agent.
  It reads words only; it does not detect cloned voices (B2 audio-deepfake model not approved or installed). Hindi transcripts have word errors.
  `assets/fonts/NotoSansDevanagari.ttf` (OFL) is for the correction card, drawn in the browser because Pillow here lacks raqm.
- Not done: Telegram bot (needs a fresh token), escalation never fired live, TruFor false-alarm rate on real social-media images
  unknown, README with a real-vs-roadmap table. `tests/unit/test_trace_sse.py::test_leaving_mid_run...` failed once under full-suite load and
  passes alone (timing).
- Official text and contrast refutation (friend's map A1, A2): `sources/official.py` + `sources/index.py` fetch the RBI press-release and SEBI
  RSS feeds (Tier 1, `runtime.official.feeds`, host gate `policy.allowed_domains`) into an SQLite FTS5 index (`data/official.sqlite3`, refreshed
  when older than `refresh_s`). **PIB, MoHFW, PMIndia and cybercrime.gov.in answer 403 to our honest User-Agent (even robots.txt); we do not
  disguise the client, so they are not used.** `claims/contrast.py`: Gemini only says same subject / same quantity and quotes the two number
  spans; code requires both spans verbatim in their texts, both parse as one number, and the numbers differ beyond `contrast_rel_tol`; then
  Evidence route `contrast` (role contradicts, tier of the passage) and `rules.decide` gives REFUTED, medium. A matching number never gives SUPPORTED.
  Live check 2026-10-31: a claim with "50,000 Crore" against an RBI notice of "29,900 Crore" refuted; the correct number and an unrelated
  number did not (one claim each, not a measured rate). Only the 10 latest RBI releases and 30 SEBI titles are indexed, so most claims retrieve nothing relevant.
- Scam and next steps (friend's map S2, S1): `scam/links.py` + `config/official_domains.yaml` check link NAMES only (never opened) against 27 listed
  official domains (DNS-resolved 2026-10-31): official, lookalike (embedded real domain, look-alike characters, near-miss, `@` trick),
  similar_name, shortened, gov_style, unknown (never "safe"); `GET /links/check`. `act/plan.py` + `config/actions.yaml`: intent (money_lost,
  scam_forward, false_claim) by plain word lists, routes (1930, cybercrime.gov.in, Chakshu on sancharsaathi.gov.in, tell your bank) with source
  URLs and a plain-text evidence pack that cites ledger entries; `POST /act/plan`; UI "what to do next" card. **The routes come from secondary
  sources (Vikaspedia, news); the official sites were not opened by us (cybercrime.gov.in answers 403), `confirmed_on_official_site: false`.** Nothing is filed.
- `scripts/preflight.py` (keys by name, every source, TruFor+Smaug service, ffmpeg, indexes, ledger, API) and `scripts/start_demo.ps1` (starts both
  services if down, then preflight) are the cold-start path.
- Origin and spread: clusters keep their earliest `members_kept` posts (in memory, forgotten when a cluster goes idle, not persisted);
  `fastclock/trace.py` builds first post, spread timeline, wording versions with word diffs; `GET /fastclock/cluster/{id}`,
  `GET /fastclock/match?text=` (a pasted claim matches a live cluster of at least `match_min_posts` posts); UI trace panel. Accounts are
  anonymous numbers, Bluesky only, "first we saw" is not the true origin. Real clusters on the quiet stream are small (mostly bot templates).
- AI-generated images: `forensics/service.py` also loads the Smaug detector (Abacus.AI, Apache 2.0, weights in git-ignored
  `forensics/models/smaug`, loaded through sentence-transformers with our own preprocessing; the repo's .py is not run) and returns `ai_score`.
  `media.py` labels SYNTHETIC_SUSPECTED (not SYNTHETIC: one detector alone) at or above `smaug_ai_generated_score` (0.5). Measured on 40 Commons AI files vs 40 random files
  (`docs/calibration/smaug_2026-10-31.txt`): 36/40 caught, 1/40 flagged (a satellite image); misses were logos and screenshots. Not tested:
  social-media re-uploads, newer generators. Video frames are not scored by it yet. Plan to the deadline: `docs/PLAN_TO_DEADLINE.md`.

## Secrets (read this twice)

- Keys live only in a git-ignored **`.env`** at the repo root, loaded by `satyasutra.envfile.load_env_file` (names only, never values).
  Names are in `.env.example`: `GEMINI_API_KEY`, `GOOGLE_FACTCHECK_API_KEY`, `DATA_GOV_IN_API_KEY`, `TELEGRAM_BOT_TOKEN`,
  `FORGE_CONTACT`, optional `FORGE_AUTHOR_SALT`.
- A hook blocks shell commands that name `.env` files. Never read or print key values, never put the user's email in request
  headers except through `FORGE_CONTACT` (which the user set on purpose for the Wikimedia User-Agent).
- Errors and logs must not echo keys. httpx exception text can contain the URL with the key, so adapters report the exception
  class name only. Tests assert this.
- Several keys were pasted into chat during the build. Treat the Telegram token (valid) as exposed and re-issue before any public demo.

## Layout

```
config/        sources.yaml (candidate sources, tiers, auth env names), policy.yaml (gate, budgets, gemini, verdict_rules rating table),
               thresholds.yaml (calibrated values only, each with data, note, date), runtime.yaml (ingest, factcheck, fastclock, extract,
               verify, provenance, probe), env_check.yaml, prompts/
src/satyasutra/
  config.py    strict pydantic config (extra=forbid), load_config(), checksum
  errors.py    typed errors: Unreachable, RateLimited, SchemaChanged, QuotaExhausted, AuthRefused, ContentBlocked, ConfigError
  schemas/     Post, Claim, Evidence, Verdict, ... ; validators coerce unsupported verdicts to UNVERIFIABLE_YET / INCONCLUSIVE
  ledger/      SQLite hash-chained append-only ledger; verify walk reports first bad seq
  sources/     probe.py (reachability, build_user_agent), factcheck.py (Fact Check adapter), bluesky.py (fetch a post by at:// URI)
  ingest/      jetstream.py (Bluesky Jetstream to Post)
  fastclock/   normalise, minhash, cluster (LSH), clock (result cache)
  llm/         gemini.py (GeminiClient, structured JSON, 15 RPM spacing)
  claims/      extract.py (post to claims, quotes must appear verbatim), rules.py (verdict rules v0, no model)
  provenance/  fingerprint.py (SHA-256, pHash, PDQ, mirrored), index.py (SQLite of hashes and links), feeds.py, build.py
  fastclock/escalate.py  Escalator: audits the fastest-spreading clusters above escalation_min_authors_per_min once each, within Gemini RPM minus a reserve; off by default (escalation.enabled); GET /fastclock/escalations
  fastclock/worker.py  live task: Jetstream -> bounded queue (drops oldest, counted) -> fast clock; /fastclock/top and /fastclock/stats; off by default (fastclock.worker_enabled)
  verdict_cache.py  /verify result cache: key = normalised text, miss on other config/code/ledger, shorter TTL when undecided, single-flight
  pipeline.py  verify_post: extract -> Fact Check -> rules -> one ledger row per verdict
  cache.py, ratelimit.py, envfile.py
  agent/       trace.py: LangGraph run -> typed trace events (the real agent graph is Phase 3)
  api/app.py   FastAPI factory; POST /verify takes {"text"} or {"uri"}; GET /trace/stream?job= streams trace events as SSE
scripts/       probe_sources, tail_posts, measure_gemini, check_env, calibrate_fastclock, calibrate_factcheck, cluster_live, extract_live,
               verify_live, build_index, match_image, measure_image_match, check_no_fixtures, verify_ledger
docs/          plan/ (the SATYASUTRA plan: BIBLE, BUILD_MAP*, source of truth for design), PHASES.md (with a gate log), spikes.md,
               env_matrix.md, gemini_limits.md, ingest_rate.md, probe_results.md, licences.md, provenance_index.md, calibration/
tests/unit, tests/contract
```

The plan documents in `docs/plan/` are the design authority. Key ideas: two verdict axes (claim labels SUPPORTED, REFUTED,
MISSING_CONTEXT, UNVERIFIABLE_YET, NOT_A_CLAIM; media labels AUTHENTIC_IN_CONTEXT, OUT_OF_CONTEXT, EDITED, SYNTHETIC, INCONCLUSIVE),
only Tier 1 or 2 evidence can decide a claim, "cannot tell" is a valid result, a no-model fast clock in front of a LangGraph agent.

## State at the time of writing

- **Phases 0 and 1 done and merged to `main`** (tags `phase-0-complete`, `phase-1-complete`). Phase 1: 10 of 10 tickets, gate logged in
  `docs/PHASES.md`, CI green. The branch `phase-1/walking-skeleton` is kept. **Phase 2 (fast clock) tickets F2-01 to F2-07 are written** on branch
  `phase-2/fast-clock` (images to fingerprints, media clusters, verdict cache, worker with counters, budgeted escalation, SLO benchmark,
  gate run). F2-03 (verdict cache), F2-04 (worker) and F2-05 (escalation; a live window escalated nothing, so the audit path has only run via fakes) are done; the rest are not started.
- The walking skeleton works: `POST /verify` takes text or a Bluesky post URI, extracts claims with Gemini, looks them up in the Fact
  Check API, applies the verdict rules and writes one ledger row per verdict. On 8 live stream posts only 1 held a claim; most posts
  are opinion or chatter. A live REFUTED or SUPPORTED end to end has not been seen.
- Phase 3 to 9 tickets are not written yet; write them when each phase starts.
- Open: TruFor `LICENSE.txt` not read; organiser question Q15 unanswered in writing; Gemini per-day quota not measured; Telegram echo
  untested and the exposed token must be re-issued; MiniCheck needs the user's approval to install; CUDA torch for the RTX 4060
  undecided; `cluster_jaccard` should be recalibrated during a real news event; extraction quality on real news and Hindi claims
  unproven; fact-checker sites' terms and robots rules not read; Pillow and numpy licences not read.

## Findings that constrain the design

- **Gemini:** use `gemini-3.5-flash-lite` (median 1.2 s, structured output works). Free tier is **15 requests/min per model**.
  `gemini-2.5-*` return 404 for new keys, and `gemini-3.8-flash` timed out or returned 503. Listed models are not all usable.
- **Ingest volume:** about 50 events/s from Jetstream, about 11 posts/s pass the filters (about 665/min). That is far above the
  Gemini limit, so the fast clock must cluster and rank before any model call.
- **Post language tags are author-declared and unreliable** (a Portuguese post was tagged `en`). Do language ID in claim extraction.
- **Fact Check API:** ratings are free text, sometimes sentences. Results are reviews of *similar* claims, so Evidence role is
  `context`, and F1-07 must treat unmapped ratings as not decisive. An empty result is `200 {}`, a bad key is `400`.
  Most realistic news claims return nothing; `UNVERIFIABLE_YET` will be the common verdict.
- **Fact Check matching:** the API returns reviews of similar claims, often of the OPPOSITE claim ("Smoking doesn't cause cancer: False"
  for "Smoking causes lung cancer"). The verdict rules therefore let a review decide only when it is near-identical to the claim
  (`factcheck_relevance` 0.9) with matching negation; everything else is shown as `related` and never decides. No positive ("true")
  rating has been seen live, so the `supports` group of the rating table is empty. `UNVERIFIABLE_YET` is the usual verdict.
- **Reverse image search is out** (Cloud Vision is paid). Provenance uses our own debunked-image index (F1-10, `docs/provenance_index.md`):
  PDQ distance <= 40 bits (`image_match_pdq_distance`, measured on 60 Commons images: 0 false matches up to 96, edits within 36).
  Crops do not match. A match means "appeared in a fact-check", never "false". Feeds carry no structured rating. Manual Lens/Yandex
  lookups remain for demo items. `pdqhash` is built from source (needs a C++ compiler).
- **LangGraph 1.2.18 streams on Python 3.14** (`astream(stream_mode=["tasks"])`, first SSE event about 16 ms). Its task `error` field holds the
  exception message, so the trace reports only the class; the state schema must declare every key a node returns or LangGraph drops it.
- **Wikimedia** blocks any request without a filled-in User-Agent: always use `build_user_agent(config.runtime.probe.user_agent)`, never
  the raw template. Its random-file API caches identical requests.
- **Python 3.14:** torch (CPU), onnxruntime, mediapipe, faster-whisper install and import. The PyPI torch wheel has no CUDA, so the
  RTX 4060 is unusable until a CUDA build is found or TruFor runs as a service on Python 3.11 (installed, uv-managed). Python 3.12
  is not installed. MiniCheck was **not** installed: the plan installs it from a git branch, which needs the user's approval.
- Wikimedia returns 403 without a descriptive User-Agent; `FORGE_CONTACT` fills it (probe shows Wikidata reachable).
- Bluesky AppView search is 403 without login; use Jetstream. `describeServer` is 501.
- Open: TruFor `LICENSE.txt` not read, organiser question Q15 (reuse of ideas from the earlier plan) unanswered in writing,
  Gemini per-day quota not measured, Telegram echo not tested.

## Conventions

- Match the surrounding code: small modules, typed, docstrings that say *why*, no comments restating code.
- Adapters raise the typed errors in `errors.py`; an empty answer is a normal result, never an exception.
- Windows: `with sqlite3.connect(...)` does not close the connection; use `contextlib.closing` or temp files cannot be deleted.
- Tests use real-shaped data: local HTTP or websocket servers that speak the response shape captured from the live API, and
  contract tests (`-m contract`) that hit the real service. No fixtures files in `src/`.
- Windows console is cp1252: scripts that print post text call `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`.
- Line endings: `.gitattributes` handles CRLF; the warnings on commit are harmless.
