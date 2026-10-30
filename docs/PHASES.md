# FORGE phases and ticket workflow

FORGE is the build repository for the SATYASUTRA plan in [`docs/plan/`](plan/). The plan says *what* and *why*; this repository
is where it gets built, in phases, one ticket at a time.

## How work flows

1. A **phase** is a group of tickets that ends in something runnable (a "gate"). A phase is closed only when its gate command passes.
2. A **ticket** is a markdown file in [`tickets/`](../tickets), `F<phase>-<nn>.md`, with a goal and a **Done when** command.
3. `python tools/forge.py start F0-03` marks it `doing` (refuses if a dependency is not done).
4. Build it. Tests first where the ticket says so.
5. `python tools/forge.py done F0-03 --evidence "pytest tests/unit/test_schemas.py"` closes it, recording date, evidence and commit.
6. **One commit per finished chunk**, message `F0-03: short title` (several tickets in one commit only when they are inseparable).
   The ticket file change goes in the same commit as the code that satisfies it.
7. Branch per phase: `phase-0/foundation`, `phase-1/...`. Merge to `main` only when the phase gate passes.
8. New work discovered mid-phase becomes a new ticket (`forge.py new`), not a silent addition.

`python tools/forge.py board` shows progress; `python tools/forge.py check` validates the ticket graph (CI runs it).

## Rules that never bend (from the plan)

- No URL, key, threshold or demo data in `src/` (`scripts/check_no_fixtures.py` enforces it). Recorded data lives in `tests/` only.
- Every verdict needs evidence; "cannot tell" is a result (`UNVERIFIABLE_YET`, `INCONCLUSIVE`).
- A ticket is not done on belief. It is done when its command passes and the evidence is written in the ticket.

## Phases

| Phase | Gate (what must run) | Maps to plan |
|---|---|---|
| 0 Foundation | `pytest -q` green, `/health` and `/ledger/verify` respond, `probe_sources.py` prints a real table, CI checks pass | Build journey 0; hour 0 to 4 |
| 1 Walking skeleton | A live post becomes a claim, a fact-check lookup and a cited verdict with a ledger row | Journey 1; PL-2 |
| 2 Fast clock | Same text twice returns the cached verdict; clusters ranked by velocity | Journey 2; PL-3, PL-4 |
| 3 Claims and agent | Verifier, retrieval, rules, contrast refutation, LangGraph agent, gate | Claims map C0 to C11 |
| 4 Provenance | Reverse search returns an earlier appearance; variant graph | Provenance map |
| 5 Languages | A Hindi claim returns a verdict and the original text | Journey 5 |
| 6 Audio forensics | Gate, two detectors, calibration by codec condition | Forensics map 5A |
| 7 Image and video forensics | TruFor heatmap, pulse-lite panel, video assembly | Forensics map 5B, FO-24, FO-PL, FO-26 |
| 8 Fusion, bot, UI | Two-axis verdicts, Telegram bot and correction card, dashboard | Journey 7, 8 |
| 9 Hardening | Drills, evaluation, claim sheet, rehearsal | Journey 9 |

Tickets for a phase are written when the phase starts, so they reflect what the previous phase taught us. Phase 0 tickets
exist now. Spikes that decide go or no-go (reverse-search key, TruFor on this machine, LLM access) are Phase 0 tickets because
the plan's review found them to be the biggest risks.

## Commit convention

```
F0-03: freeze core schemas

What changed and why in two or three lines. Reference the plan section if useful.
```

## Gate log

- **Phase 1 gate command passes (2026-10-31, F1-09).** A post taken live from the Bluesky stream goes through Gemini claim extraction, a Fact Check lookup and the verdict rules, and each verdict gets a ledger row (`tests/contract/test_walking_skeleton.py`, `scripts/verify_live.py`). Phase 1 was still open at that point for F1-03 and F1-10. On 8 live posts, 1 produced a claim (a stock-trade report, `UNVERIFIABLE_YET`, no reviews); most posts are opinion or chatter.
- **Phase 1 complete (2026-10-31).** All 10 tickets done: F1-03 (LangGraph to SSE) and F1-10 (debunked-image index) closed after the gate command above passed. 269 unit tests, the guard and the live contract tests pass. Merged to `main` and tagged `phase-1-complete`.
