# Demo runbook (package G3, rehearsal notes)

What to show, in what order, with the command or page behind each step. Every item below was run live by us on the dates given; none is a recording. Circulating items must be
re-checked on the day: fact-checks and news change, so a result seen on 31 Oct 2026 can differ.

## Cold start

```
powershell -File scripts\start_demo.ps1        # starts the GPU service and the API if they are down, then runs preflight
python scripts\preflight.py                    # keys by name, every source, services, indexes, ledger, API: all must be green before anyone watches
python scripts\bench_pulse.py --runs 1         # optional: proves the feeds answer from this network today
```
Rehearse twice in a row with no edits; once on the venue network. If a feed or a model is down, say so on stage: the system reports it too.

## Order of the demo

| # | Show | Where | What it proves | Seen live (date) |
|---|---|---|---|---|
| 1 | What is spreading in India now | `/officials`, home panel | Radar: 23 live feeds, no model calls, grouped by story; says it is not WhatsApp | 31 Oct |
| 2 | A claim a fact-checker covered: "COVID-19 vaccines contain microchips" | home, claim box | REFUTED, with the FactCheck.org review, the rule trail and "Why this verdict" | 31 Oct |
| 3 | A claim only news can settle: "Chandrayaan-3 landed near the lunar south pole" | home | SUPPORTED from two reputable outlets (BBC, Guardian), confidence capped at medium | 31 Oct |
| 4 | A changed number against RBI text | home, then the Telegram bot | REFUTED from official text; the bot now runs the same path as the page | 31 Oct (page); bot path tested with fakes only |
| 5 | A claim the system cannot settle: an invented assassination or an invented RBI ban | home | "Cannot tell" is a result, not a failure | 31 Oct |
| 6 | A Hindi claim and a Kannada voice note | home, voice drop zone | Translation, transcript shown first, second opinion when Whisper is unsure | Hindi 31 Oct; Kannada one machine voice only |
| 7 | An edited photo and an AI-made photo | image drop zone | EDITED at TruFor 0.7; SYNTHETIC_SUSPECTED at Smaug 0.5; never "authentic" | calibration files in `docs/calibration/` |
| 8 | A scam forward with a look-alike link | notice or link check, "what to do next" | Link names only (never opened), routes 1930 and the cyber-crime portal; nothing is filed | 31 Oct |
| 9 | The Trust Report for one of the above | `/report/<id>` | Ten sections built only from server records, English and Hindi | 31 Oct |
| 10 | The ledger check | home | Every verdict hash-chained; `GET /ledger/verify` | any day |

## Say plainly on stage

- Cannot tell is common. The claim sheet gives how often (numbers come from `docs/claim-sheet.md`, with n and ranges).
- Not detected: face swaps, AI video, cloned voices of real people. Voice test was small and weak.
- Hindi and Kannada wording is ours, unreviewed by a native speaker. Indian official routes were not confirmed on the official sites (they refuse our client).
- PIB, MoHFW, PMIndia and cybercrime.gov.in answer 403; we do not disguise the client.
- Private mode: Telegram messages carry a private header and are not stored; web checks are stored on this server and visible in the command centre (no sign-in).

## Backup

Keep screenshots of one good run, labelled as screenshots with the date and time. Never present them as live.
