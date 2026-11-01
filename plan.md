# Final closing plan

Deadline: **2 November 2026, 00:00**. This plan covers everything from now to submission. It replaces `docs/PLAN_TO_DEADLINE.md` for the closing stretch.
State on 1 November: the build is merged to `main` (CI green on the branch), 592 unit tests pass, the held-out test split has been run once, and the project rates about
68 out of 100 by our own judgement. The gap to 80 is mostly evidence and reliability, not features.

## Principles for the last hours

1. **No new features after the freeze.** Fixes only. A new detector or route adds an unmeasured number that a judge can attack.
2. **Say what was not tested.** Every claim in the pitch must match a sheet in `docs/`. If a number is optimistic, we say so.
3. **No pre-recorded demos.** A recording may exist only as a labelled backup, never shown as the live run.
4. **The no-mock-data rule holds.** `scripts/check_no_fixtures.py` must pass on the final commit.
5. **The held-out test split is spent for the frozen version.** A second run needs a new freeze tag and is reported beside the first, never instead of it.

## Owners

| Owner | Who | Items |
|---|---|---|
| Team (people) | you and teammates | keys, Hindi review, labelling, route check, phone tests, rehearsal |
| Build (Claude) | this session | code, tests, sheets, docs, merge, CI |

## Checklist

Each line has an owner, what "done" means, and what to do if it cannot be done.

### A. Keys and accounts (do first; everything live depends on these)

- [ ] **Gemini key with headroom** (team). Done when `scripts/preflight.py` shows Gemini answering and a 3-user load test (`scripts/load_gemini.py --users 3`) has no busy answers.
  If not possible: keep the demo to 3 people checking at once and say the free-tier limit out loud.
- [ ] **Rotate the exposed Gemini key** (team). The old one was pasted into chat. Done when the old key is revoked.
- [ ] **Fresh Telegram token** (team) in `.env`, then restart the bot. Done when `/help_me` and a text claim answer in a real chat.
  If not: demo the bot from screenshots taken live that day, labelled as such, or drop the bot from the pitch.

### B. Evidence (raises the accuracy score; needs people)

- [ ] **Second labeller** (team) re-labels the U, R and B rows of `tests/eval/` blind. Done when disagreements are listed in the sheet and resolved by discussion, not by editing results.
- [ ] **Second test run** (build, after B-1). New freeze tag on the final code, one run, reported next to the first in `docs/claim-sheet-test.md`. This is the only way the recency and forecast
  fixes get a held-out number. If it fails again, the failure is written beside the numbers.
- [ ] **Lookup-on test run** (build, optional, needs quota). Reported separately as a different configuration.

### C. Wording a judge can read (needs a native speaker)

- [ ] **Hindi review** (team) of: Call guard (`config/scam_script.yaml`), Raksha (`config/raksha.yaml`), correction cards (`config/cards.yaml`), Trust Report Hindi text.
  Done when `reviewed_by` and `reviewed_on` are filled in the files that have them and the corrections are committed.
  If not done: the README and the pitch already say the Hindi is unreviewed; keep that line.
- [ ] **Route check** (team): open the official pages for 1930, cybercrime.gov.in, Chakshu, StopNCII and Take It Down; confirm the steps in `config/actions.yaml`.
  Done when `confirmed_on_official_site` is true only for routes someone opened.

### D. Polish (build, no one else needed)

- [ ] Walk every page at phone width: `/`, `/guard`, `/help`, `/my-investigations`, `/report/<id>`, `/officials`, `/command`. Fix overflow and unreadable text.
- [ ] Click through the new pieces once in a real browser: the family-alert link, the delete button, the how-to box, the nav links after a hard reload.
- [ ] Read what each page says when Gemini is out, with the server running and the key blocked (use a wrong key in a scratch run, not the real `.env`).
- [ ] Remove dead links and stale pages; check the README links resolve.
- [ ] Optional builds if time remains, in this order: "where reported" timeline; names and places in the contrast check; more language rows.

### E. Demo (team and build together)

- [ ] **Pick the live items** (team): one real forwarded rumour that returns a verdict, one real image, one photographed notice, one scam-call script said aloud. Test each twice on the final code.
- [ ] **Cold-start rehearsal** on the venue network or a phone hotspot: `scripts\start_demo.ps1`, then `scripts\preflight.py`. Done when all sources and services show ready within 5 minutes.
- [ ] **Three timed run-throughs** of the pitch (`docs/PITCH.md`, `docs/DEMO_RUNBOOK.md`) at 3 minutes.
- [ ] **Fallbacks** written in the runbook: Gemini busy, network down, GPU service down, microphone refused. Each has one sentence to say and one screen to show.

### F. Close (build)

- [ ] **Freeze** at least 4 hours before the deadline: no merges except fixes the rehearsal finds.
- [ ] Final `pytest -q`, `scripts/check_no_fixtures.py`, `tools/forge.py check`. All pass.
- [ ] README and pitch numbers checked against the sheets in `docs/` (held-out test, Call guard, Raksha, load, TruFor, Smaug, voice).
- [ ] Merge to `main`, confirm GitHub CI is green on the merge commit, tag `final-2026-11-01`, push the tag.
- [ ] Confirm the public repository shows the README, the sheets and the tag. No `.env`, no logs, no model weights.

## Suggested order and time boxes

| Block | What | Needs |
|---|---|---|
| 1 (first 2 h) | A: keys and token. D: phone-width pass, click-through | team, build |
| 2 | B: second labeller starts; build the timeline if time allows | team, build |
| 3 | C: Hindi review and route check; fold corrections in | team, build |
| 4 | B: second test run on a new freeze tag | build, quota free |
| 5 | E: pick demo items, cold-start rehearsal, three run-throughs | team |
| 6 (last 4 h) | F: freeze, final checks, merge, tag, push | build |

If a block slips, drop it from the bottom of its own list, not from the freeze.

## Risks and what we say about them

| Risk | If it happens |
|---|---|
| Gemini quota runs out on stage | Say it is the free-tier limit, show the busy message (it says what happened and does not guess), switch to a typed claim already in the verdict cache |
| A claim returns "cannot tell" in the demo | Good: that is the design. Say why and show the evidence panel |
| The web rule calls an old story true | The held-out sheet already shows this failure; point to it and to the recency check, and say the fix is unmeasured |
| Hindi text has an error | State up front that the Hindi is unreviewed |
| A judge asks about real WhatsApp spread | The stream is Bluesky only and the pulse reads public feeds; WhatsApp is not seen |
| A judge asks whether Call guard works on real calls | It was tested on 32 written scripts we wrote, with optimistic scores; no real calls |

## What we will not do

- Disguise our client to get past a bot wall (PIB, MoHFW, PMIndia).
- File to any government portal or defeat any CAPTCHA.
- Show a recording as the live demo.
- Edit labels or thresholds to improve a number after seeing the held-out result.
- Say "authentic" about media, or "safe" about a call.
