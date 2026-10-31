# Spec: human replies, news spread, Gemini load test, and the verdict dataset

Written Sat 31 Oct 2026, about 15:50 IST, against `phase-8/prototype-ui` at `27f4c9d`. Deadline: **Mon 2 Nov 2026, 00:00 IST**, which is
midnight at the end of Sunday 1 Nov. Checkpoints and cut order are in [`BUILD_PLAN.md`](BUILD_PLAN.md); this spec adds four packages
to it. Hours are estimates. Everything here follows the standing rules: no mock data in `src/`, values in `config/`, a detector or
source that finds nothing is never "authentic", and nothing is filed on anyone's behalf.

| ID | Package | Hours | Owner | Files (new unless noted) |
|---|---|---|---|---|
| R3 | Gemini load test and quota plan | 1.5 | any session | `scripts/load_gemini.py`; counter in `llm/gemini.py` (integrator applies) |
| D1 | Verdict dataset and evaluation harness | 4, then 1 per iteration | one person who did **not** write the claims code | `tests/eval/`, `scripts/eval/`, `docs/claim-sheet.md` |
| R1 | Human reply layer | 3 | bot owner | `src/satyasutra/reply/compose.py`, `config/prompts/reply.txt`, `config/reply.yaml`; wiring in `bot/telegram.py` and `api/app.py` (integrator) |
| R2 | "Where has this been reported" timeline | 3 | any session | `src/satyasutra/sources/spread.py`; endpoint and UI panel (integrator) |

Order: **R3 first** (30 minutes, its result decides how much Gemini the other packages may use), **D1 in parallel** with R1 and R2,
because D1 needs a different person and different files.

---

## R3. Gemini load test and quota plan

**Why.** The free tier allows 15 requests per minute per model (`docs/gemini_limits.md`). One user message can cost several calls
(claim extraction, same-claim judge, entity grounding, contrast check, and soon the reply writer). A few judges on their phones at
once could stall the bot, and nobody has measured what the bot says when that happens. The fallback model `gemini-3.8-flash` timed
out in the earlier test (0 of 3 succeeded), so it cannot be assumed to help.

**Build.**
1. Add a call counter to `GeminiClient`: calls, successes, 429s, 5xx, timeouts, retries, fallback uses, per purpose label
   (`extract`, `judge`, `ground`, `contrast`, `reply`). Expose it on the existing `GET /metrics/slo` or `/health`.
2. `scripts/load_gemini.py --users N --messages M --seconds S`: sends M distinct claims per simulated user to the local API
   (distinct texts, so the verdict cache cannot hide the cost), N users at once, and prints a table.

**Measure and write into `docs/gemini_limits.md`:**
- calls per message, by purpose, on text claims, Hindi claims and a voice note;
- success rate, 429 rate, 5xx rate, median and 95th percentile reply time at N = 1, 3, 6, 10 users;
- what the user receives when the quota is exhausted (the exact text), and how long until the bot recovers;
- whether the fallback model ever succeeded during the test.

**Decide from the numbers (record the decision in the doc):**

| Result | Action |
|---|---|
| Calls per message is 4 or more, or any reply took longer than 20 s at 6 users | Cut calls first: skip judge and grounding for claims with no fact-check hits; run the reply writer only when the quota guard has spare capacity |
| The bot times out or hangs at 6 users | Put a queue in front of the bot with a "busy, I'll reply in about N seconds" message; never a silent wait |
| Still not safe at 6 users | Get a paid-tier key as the demo key (a team decision, spending money); keep the free key for development |
| Safe at 6 users | Record the tested ceiling in the pitch's feasibility slide: "tested with 6 simultaneous users" |

Do not rotate several free keys to dodge the limit: that goes against the provider's terms. The cache, the rate limit per user and a
paid key are the legitimate tools.

**Done when:** `docs/gemini_limits.md` has the table and the decision, and the counter shows in the sources panel.

---

## D1. The verdict dataset and evaluation harness

**What it answers.** How often is each verdict right, how often does the system refuse to answer, and does "medium confidence"
mean what it says. Today none of this is measured on claims (CLAUDE.md says the news matcher and contrast refutation were each
"checked on a few examples, not on a labelled set").

### 1. What is being predicted
The final claim label from the agent for one claim: `REFUTED`, `SUPPORTED`, `MISSING_CONTEXT`, `UNVERIFIABLE_YET`, `NOT_A_CLAIM`, plus
its confidence band. The dataset stores the **correct answer** for each claim, built in a way that does not depend on the system under
test.

### 2. Four groups (about 120 claims, plus 15 Hindi)

| Group | n | How each claim is built | Correct answer |
|---|---|---|---|
| **F: false, published** | 30 | Take claims from published fact-checks rated false (the Fact Check API's `claimReview` records, and Alt News, BOOM or Factly feeds already in `sources.yaml`). Use the *claim text as the checker wrote it*, not our paraphrase. Keep the review URL and date | `REFUTED` |
| **T: true, official** | 30 | Take one factual sentence from a recent official release we can reach (RBI, SEBI, and any official source the probe finds reachable) and write it as a claim, copying the figures exactly | **Not `REFUTED`.** Today the system cannot return `SUPPORTED` from official text (a matching number never gives SUPPORTED), so the measured outcome is "was it falsely refuted", plus a separate `SUPPORTED` rate that stays near zero until a support rule exists |
| **C: changed number** | 30 | Take each T claim and change **one** number or date **by code, not by hand or by Gemini** (scale a figure, shift a date), recording what changed | `REFUTED` via contrast, citing the official passage |
| **U: unknowable** | 30 | Predictions ("the rupee will fall next month"), opinions, very local or very fresh claims, and invented-but-plausible claims with no source anywhere | `UNVERIFIABLE_YET` or `NOT_A_CLAIM`; any `REFUTED` or `SUPPORTED` is an error |
| **H: Hindi** | 15 | A teammate who reads Hindi translates 5 claims each from F, C and U | Same as the original |

### 3. Rules that keep it honest
1. **Fact-check lookup is switched off** when running F. Otherwise the system simply finds the review the claim came from. Do it
   without touching `src/`: the harness builds the app with `create_app(lookup=<function returning an empty result>)`, which
   the API already accepts. Run F **again with lookup on** and report it as a separate row ("coverage when fact-checkers exist").
2. **Two labellers for F and U**, working separately. A claim they disagree on is dropped, not argued over. T and C labels come
   from the source text and the code that changed it, so they need no judgement.
3. **Date-stamp everything.** A claim's correct answer can change (a rumour later becomes true). Each row stores `source_date` and
   `built_on`.
4. **Split before looking at any result:** `split = dev` if `sha256(id)` mod 3 is 0, else `test`. Tune only on `dev`. Run `test`
   **once per frozen version** and never use it to adjust anything.
5. **Leakage check:** no `test` claim may be in a prompt, a threshold note or a unit test. The harness refuses to run if a `test`
   claim text appears anywhere under `src/`, `config/` or `tests/unit/`.
6. **Source terms:** store claim text, the publisher name, the date and the URL only, never article bodies or images. Read each
   publisher's terms before the dataset is shared outside the team (they were not read for the news feeds either). Official
   releases are fine to quote briefly with attribution.

### 4. File layout and format
```
tests/eval/claims.jsonl        one JSON object per line; the only place claim data lives (never in src/)
tests/eval/README.md           how each group was built, who labelled, date, known weaknesses
scripts/eval/build_f.py        pulls false claims from the Fact Check API and feeds, writes candidate rows for labelling
scripts/eval/build_t.py        pulls official releases and writes candidate T rows
scripts/eval/make_c.py         derives C rows from T rows by changing one number or date in code
scripts/eval/run.py            runs the harness and prints the tables
docs/claim-sheet.md            the results, each row with its command and date
```
Row fields: `id, group, text, lang, expected, expected_set, source_url, source_date, built_from, change (for C: what was changed),
labelled_by, split, built_on, notes`. `expected_set` lists every acceptable label (for T: all but `REFUTED`).

### 5. The harness (`scripts/eval/run.py`)
- `--split dev|test`, `--groups F,T,C,U,H`, `--factcheck off|on`, `--out docs/claim-sheet.md`, `--limit N` for a quick run.
- Calls the same agent the bot uses, one claim at a time, respecting the Gemini rate (so a full run takes about 10 minutes at 15
  per minute; plan for it, and use `--limit` while developing).
- Writes every prediction, its band, its route and its reason trail to `tests/eval/runs/<date>-<git-sha>.jsonl` so any wrong answer
  can be inspected later.
- Refuses to run `--split test` unless the working tree is clean and the run is tagged with the commit (so the test number is tied
  to a frozen version).

### 6. Numbers to report, each with n and a 95 percent range (Wilson interval)

| Metric | Definition | Why it matters |
|---|---|---|
| **False-refute rate** | T claims labelled `REFUTED`, out of T | Calling a true thing false does harm; must be at or near zero |
| **False-support rate** | F and C claims labelled `SUPPORTED` | Believing a hoax |
| **Refute recall (no fact-check)** | F (lookup off) and C labelled `REFUTED`, out of F and C | The headline claim: refuting before fact-checkers |
| **Coverage** | Share of F, T, C that gets any decided label | Shows how cautious it is, which your screenshots suggest is a lot |
| **Precision by band** | Correct share among medium and among high answers | Whether the confidence words are honest |
| **Correct abstention** | U answered `UNVERIFIABLE_YET` or `NOT_A_CLAIM` | Proves it does not guess |
| **By route** | Same table for fact-check, official text, contrast, news, Wikidata | Where the hours should go |
| **Hindi gap** | H versus the English originals | The multilingual claim |
| **Latency** | Median and 95th percentile per claim | The real-time claim |

Report small samples as ranges ("28 of 30, between 79 and 98 percent"), never a bare percentage. With 30 per group, differences
of a few claims are noise.

### 7. Build plan
| Step | Who | Time |
|---|---|---|
| Write `build_f.py`, `build_t.py`, `make_c.py`, collect candidates | person running D1 (with a Claude session) | 1.5 h |
| Label F and U (two people, separately), translate H | two people | 1 h |
| `run.py`, interval maths, claim-sheet writer, leakage check | Claude session | 1.5 h |
| First dev run and the first error list | D1 person | 1 h (about 10 minutes of it is waiting) |

**Gate:** by Sat 22:00 (CP2) `tests/eval/claims.jsonl` exists with all four groups and the first dev table is in
`docs/claim-sheet.md`. If hours run short, cut H first, then halve F and U, and keep C: it measures the one feature that
differentiates the project.

### 8. Known limits to state in the claim sheet
The T and C groups depend on official text we can reach, which today is RBI and SEBI finance releases (PIB, MoHFW and cybercrime.gov.in
return 403). So these numbers describe finance claims, **not general accuracy**. F claims are fact-checked claims, which skew toward
viral topics. A 120-claim set cannot show rare failures. The labellers are the team, not independent reviewers.

---

## R1. Human reply layer

**Problem.** Replies are built from fixed sentence templates in `config/runtime.yaml` (`telegram.labels`, `telegram.messages`).
They are safe and no longer read like logs, but every answer has the same shape, and "I can't confirm this one" feels like a
shrug. Gemini can write like a person, but it must not be able to change the verdict or invent facts.

**Design.**
1. The rules decide the verdict, evidence and links exactly as now. Nothing about that changes.
2. A new `reply/compose.py` builds a **facts object** from the result: the claim in the user's language; the label; the band; the
   deciding source (publisher, rating, URL); news coverage (outlets that report it, or "none of N outlets, searched back to T");
   any official-activity facts; the next step (from `act/plan.py`); the topic risk class (below); the reply language.
3. One Gemini call turns the facts object into a JSON reply, using `config/prompts/reply.txt`:
   `{"reply": "...", "stance": "<one of the verdict labels>"}`. The prompt asks for a warm, plain voice like a well-informed
   friend: short sentences, no jargon (no "band", "tier", "route"), say what we found, why we believe it, what to do next, in
   the reader's language, at most `reply.max_chars` characters (Telegram's limit is 4096; aim far lower).
4. **Guards, in code, before anything is sent. Any failure sends the template reply instead:**
   - `stance` equals the verdict the rules chose;
   - every URL in the reply is in the facts object's allowed list;
   - every number and date in the reply appears in the facts object;
   - no sentence states a source or outlet that is not in the facts object;
   - the reply is in the requested language (script check for Hindi);
   - the reply contains no certainty words beyond the label (for `UNVERIFIABLE_YET`, none of "definitely", "100%", "confirmed");
   - length within the limit.
5. **High-risk topics** (death or attack on a public figure, disasters, health emergencies, communal incidents) are listed in
   `config/reply.yaml` with their guidance: for `UNVERIFIABLE_YET` on these, the reply says plainly that an event like this would
   be announced officially and covered everywhere, that nothing is reporting it, and asks the reader not to forward it. The label
   stays `UNVERIFIABLE_YET`; only the advice is firmer. Communal and political content never gets a firmer verdict from this layer
   (Bible D3).
6. Cache by `sha256(facts object)`. Skip the call, and use the template, when the quota guard reports spare capacity below
   `reply.min_spare_calls`.

**Before and after (target tone, written by hand to calibrate the prompt, not shipped as text):**

| Case | Template today | Target |
|---|---|---|
| "PM Modi assassinated", nothing found | "I can't confirm this one. No fact-checker I can search has looked at this claim yet. I also went through the latest stories from 8 major news outlets and none of them is reporting this..." | "I looked for this and I don't think it's real. None of the 8 big news outlets I checked mention it, and if the Prime Minister had been attacked it would be the top story everywhere. I can't prove a negative, so please don't forward it until an official source or a news channel you trust confirms it." |
| ISRO launch, two outlets report | "I can't confirm this one... In the news: ..." | "Yes, this looks right. The Times of India and Hindustan Times both report that the GSLV-F18 launch succeeded and the Prime Minister congratulated ISRO. I haven't found ISRO's own announcement yet, so treat it as very likely, not officially confirmed." (**only after the news rule is decided, see "After the dataset"**) |

**Done when:** a test with recorded Gemini replies (tests only) shows each guard rejecting a bad reply (wrong stance, extra URL,
invented number, wrong language) and falling back; a live run on 10 claims shows no guard failures that went out; two people who did not
write it agree the replies read like a person; the demo claims from Verify to Act, a refuted claim and a Hindi claim all read well.

---

## R2. Where has this been reported

**Scope, stated plainly.** We can show where a claim appeared in **sources we can legally and technically read**: the 8 news outlets
already in `config/runtime.yaml`, the fact-checkers in the Fact Check API and feeds, and the live Bluesky stream. We cannot see
WhatsApp (encrypted), and most of X, Instagram and Facebook are closed to us. The feature says so on screen.

**Build** (`sources/spread.py`, using `check_news` and the Fact Check client as they are):
1. `GET /news/spread?text=` returns:
   - `first_reported`: outlet, headline, link, time of the earliest matching item;
   - `timeline`: every matching item from every outlet, sorted by time, each with outlet, headline, link and time (items with no
     time go in a separate "time unknown" list, never guessed);
   - `fact_checks`: reviews of the same or a similar claim with publisher and review date, from the Fact Check API;
   - `on_bluesky`: the matching live cluster, if any (from `/fastclock/match`);
   - `looked_in`: outlets checked, how far back the feeds reach, and the scope note.
2. A config value `news.spread_max_items` raises the cap (today `max_results` is 4). Use the same rarity-weighted matching that
   `check_news` already uses; do not loosen `min_coverage` to get more rows.
3. UI: a "Where it has been reported" panel under the verdict, a simple timeline. Bot: one line, "First reported by <outlet> at
   <time>; <n> more outlets since", only when there are matches.
4. Optional, only after a probe says it is reachable and its terms allow it: GDELT as a wider timeline source. Not required.

**Limits to print:** RSS feeds hold only recent items, so "not found" never means "never reported"; "first reported here" is
"first we found", not the true origin; being reported is not being true (outlets also report rumours to debunk them).

**Done when:** a live claim that several outlets covered returns an ordered timeline with links; a claim nobody reported returns
`not_found` with the outlets and how far back; an outlet whose feed fails is named in `looked_in`, never silently dropped; unit tests
use a local feed server (tests only).

---

## After the dataset is built

This is the sequence that turns the dataset into a better product and into pitch numbers. Times are targets on the existing
schedule.

1. **Run the baseline on `dev`** (Sat evening). Write the table into `docs/claim-sheet.md` untouched, even if it is ugly. Expect high
   safety and low coverage.
2. **Read every wrong or abstained answer** (budget 45 minutes for roughly 20 to 30 items). Put each in a bucket: extraction missed
   the claim, retrieval found nothing, same-claim judge wrong, contrast span mismatch, news matcher too strict or too loose,
   translation error, or "genuinely unknowable". The bucket counts, not the percentages, tell you where to spend the remaining hours.
3. **Write the decision criteria for the news rule before testing it** (so you cannot bend them afterwards). Proposed, for the team to
   change before running anything: allow two independent outlets that state the claim to decide `SUPPORTED` or `REFUTED` at **medium**
   confidence only if, on `dev`, the false-support rate on F and C stays at or below 1 in 30 and the false-refute rate on T stays at 0.
   If it fails either test, the rule does not ship, and the replies keep their honest "very likely, not confirmed" wording.
4. **Add the official-activity and news rules behind config flags**, run `dev` with each flag on and off, and keep only what passes.
5. **Tune thresholds on `dev` only** (`factcheck_relevance`, `contrast_rel_tol`, the news `min_coverage`). Write each change into
   `config/thresholds.yaml` with the data, the date and the result, as the file's existing entries do.
6. **Freeze at CP3 (Sun 14:00).** Tag the commit. Run `test` once. The numbers from that run, with intervals, go into the claim sheet
   and nowhere else. If the result is worse than `dev`, report the `test` result anyway and say so.
7. **Check the media detectors under real conditions** (small, same day): run TruFor and Smaug on the same images after they have
   been through Telegram and WhatsApp compression, and on screenshots; add the weather and satellite images to Smaug's real set.
   One line per detector per condition in the claim sheet.
8. **Put the harness into the routine.** `scripts/eval/run.py --limit 20` joins `scripts/preflight.py` as a smoke check; run the full
   `dev` set after any change to rules, prompts or thresholds. Before the final submission, run it once more on the frozen build.
9. **Turn the results into the pitch and README.** Use only numbers from the claim sheet. Show one chart of coverage against
   false-answer rate. Show a "what it got wrong and why" slide from the error buckets (judges trust a team that shows its failures).
   State the limits from section 8 above in the README's real-versus-roadmap table.
10. **Feed the roadmap from the buckets.** Whatever the biggest buckets were become the "next" slide: for example more official
    sources if retrieval found nothing, a better Hindi path if translation errors dominate.
11. **After the event:** keep growing the set with newly circulating claims (opt-in only; the bot stores no message content by design),
    add an independent labeller, and publish the dataset's construction notes so others can reproduce the numbers.

## Acceptance for the whole spec

| Item | Pass when |
|---|---|
| R3 | Table and decision in `docs/gemini_limits.md`; the bot shows a clear "busy" message instead of hanging |
| D1 | `tests/eval/claims.jsonl` (about 135 rows, four groups plus Hindi), harness runs, dev table in `docs/claim-sheet.md`, error buckets written |
| R1 | Guards tested; 10 live replies pass; two readers who did not write it agree it sounds human |
| R2 | Live timeline with links; not-found and failed-outlet cases handled; scope note shown |
| Whole | `pytest`, `scripts/check_no_fixtures.py` and `tools/forge.py check` pass; no claim text in `src/`; preflight green |
