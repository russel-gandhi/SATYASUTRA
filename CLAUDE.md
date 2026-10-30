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
uvicorn satyasutra.api.app:create_app --factory      # the API (/health, /health/sources, /ledger/verify, /ledger/{seq})
```

The Bash tool is Git Bash; the user's own terminal is PowerShell (no `&&`, no `printf`). Heredocs containing apostrophes can break
the Bash tool: use the file Write tool for multi-file edits.

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
config/        sources.yaml (candidate sources, tiers, auth env names), policy.yaml (gate, budgets, gemini), thresholds.yaml
               (calibrated values only), runtime.yaml (ingest, factcheck, fastclock, probe settings), env_check.yaml
src/satyasutra/
  config.py    strict pydantic config (extra=forbid), load_config(), checksum
  errors.py    typed errors: Unreachable, RateLimited, SchemaChanged, QuotaExhausted, ConfigError
  schemas/     Post, Claim, Evidence, Verdict, ... ; validators coerce unsupported verdicts to UNVERIFIABLE_YET / INCONCLUSIVE
  ledger/      SQLite hash-chained append-only ledger; verify walk reports first bad seq
  sources/     probe.py (reachability), factcheck.py (Google Fact Check adapter, cached, rate-spaced)
  ingest/      jetstream.py (Bluesky Jetstream to Post)
  fastclock/   normalise, minhash, cluster (LSH), clock (result cache)   <- F1-08, uncommitted
  cache.py     SQLite TTL cache that keeps the original fetch time
  api/app.py   FastAPI factory
scripts/       probe_sources, tail_posts, measure_gemini, check_env, calibrate_fastclock, check_no_fixtures, verify_ledger
docs/          plan/ (the SATYASUTRA plan: BIBLE, BUILD_MAP*, source of truth for design), PHASES.md, spikes.md, env_matrix.md,
               gemini_limits.md, ingest_rate.md, probe_results.md
tests/unit, tests/contract
```

The plan documents in `docs/plan/` are the design authority. Key ideas: two verdict axes (claim labels SUPPORTED, REFUTED,
MISSING_CONTEXT, UNVERIFIABLE_YET, NOT_A_CLAIM; media labels AUTHENTIC_IN_CONTEXT, OUT_OF_CONTEXT, EDITED, SYNTHETIC, INCONCLUSIVE),
only Tier 1 or 2 evidence can decide a claim, "cannot tell" is a valid result, a no-model fast clock in front of a LangGraph agent.

## State at the time of writing

- **Phase 0 done** (9/9). **Phase 1 in progress: 4 of 10 done** (F1-01, F1-02, F1-04, F1-06).
- **F1-08 (fast clock) is `doing`** and its code is uncommitted: `src/satyasutra/fastclock/`, `scripts/calibrate_fastclock.py`,
  `tests/unit/test_fastclock.py` (19 tests pass), plus a `fastclock:` block in `runtime.yaml` and `FastClockSettings` in `config.py`.
  Still to do: finish the 5-minute live capture, run `calibrate_fastclock.py analyse`, look at the sample pairs, then
  `write --value ...` to record `cluster_jaccard` in `config/thresholds.yaml` (the threshold must come from data, not a guess;
  `FastClock` refuses to start without it), write `scripts/cluster_live.py`, close the ticket and commit.
- Remaining Phase 1: F1-03 (LangGraph to SSE), F1-05 (Gemini claim extraction), F1-07 (verdict rules), F1-09 (`/verify` skeleton),
  F1-10 (debunked-image fingerprint index). Phase 2 to 9 tickets are not written yet.
- CI (`.github/workflows/ci.yml`, Python 3.12) was queued on GitHub and had not reported when this was written. It installs
  `.[dev]`; check it, because `websockets` and later dependencies were added after the workflow was written.

## Findings that constrain the design

- **Gemini:** use `gemini-3.5-flash-lite` (median 1.2 s, structured output works). Free tier is **15 requests/min per model**.
  `gemini-2.5-*` return 404 for new keys, and `gemini-3.8-flash` timed out or returned 503. Listed models are not all usable.
- **Ingest volume:** about 50 events/s from Jetstream, about 11 posts/s pass the filters (about 665/min). That is far above the
  Gemini limit, so the fast clock must cluster and rank before any model call.
- **Post language tags are author-declared and unreliable** (a Portuguese post was tagged `en`). Do language ID in claim extraction.
- **Fact Check API:** ratings are free text, sometimes sentences. Results are reviews of *similar* claims, so Evidence role is
  `context`, and F1-07 must treat unmapped ratings as not decisive. An empty result is `200 {}`, a bad key is `400`.
  Most realistic news claims return nothing; `UNVERIFIABLE_YET` will be the common verdict.
- **Reverse image search is out** (Cloud Vision is paid). Provenance uses our own pHash/PDQ index (F1-10) and manual Lens/Yandex
  lookups for demo items.
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
- Tests use real-shaped data: local HTTP or websocket servers that speak the response shape captured from the live API, and
  contract tests (`-m contract`) that hit the real service. No fixtures files in `src/`.
- Windows console is cp1252: scripts that print post text call `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`.
- Line endings: `.gitattributes` handles CRLF; the warnings on commit are harmless.
