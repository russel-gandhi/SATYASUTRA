# Plan: Raksha (help for people targeted with private images) and Scam Guard (warnings during a scam call)

Written Sat 31 Oct 2026, about 18:00 IST, against `main` at `88371c7`. Deadline Mon 2 Nov 2026 00:00 IST (midnight at the end of Sunday 1 Nov).
This plan adds work to [`BUILD_PLAN.md`](BUILD_PLAN.md) and replaces its earlier S5 (Raksha-lite) and Call Guard ideas with something specific. Hours are
estimates. Everything follows the standing rules: no mock data in `src/`, wording and numbers in `config/`, never file or send anything on anyone's behalf
(D42), say what was not tested.

Both features exist for people at their most frightened. The rule for this whole plan: **do less, and make sure what we do is safe, honest and tested.**
A half-built safety tool that stores or leaks something is worse than none.

---

## 0. What already exists (read from the code, not from memory)

| Piece | State | Where |
|---|---|---|
| Intimate-image **intent detection** (English and Hindi phrase lists) | Built | `config/actions.yaml` (`intimate_phrases`), `act/plan.py` |
| **Seven support routes** for that intent: keep a record, do not pay, StopNCII (adults), Take It Down (minors), cybercrime portal, report on the platform, call 112 | Built, wording only | `config/actions.yaml` (`routes`, `intents.intimate_images`) |
| Plain-text **evidence pack** with ledger entries | Built | `act/plan.py`, `POST /act/plan` |
| Scam intents (`money_lost`, `scam_forward`), routes 1930, tell your bank, Chakshu, report in app | Built | same file |
| **Lookalike-link check** against 27 official domains | Built | `scam/links.py`, `GET /links/check` |
| Voice notes to text (Whisper on the GPU service, CPU fallback) | Built | `voice/transcribe.py`, `POST /voice/transcribe` |
| Synthetic-speech score (wav2vec2 classifier; small weak test set, scores only) | Built, weak | `audio/spoof.py`, `POST /audio/check` |
| Telegram bot, text, voice and photo | Built | `bot/telegram.py` |
| **Live call warning** | **Not built** | nothing in the repo |
| **Guided Raksha flow** (questions, branches, draft complaint) | **Not built** | one reply with a route list only |

Routes and numbers in `actions.yaml` come from secondary sources and are marked `confirmed_on_official_site: false`. From the machine this plan was written
on, `cybercrime.gov.in`, `sancharsaathi.gov.in`, `stopncii.org` and `takeitdown.ncmec.org` all answered 200, so a person can open and confirm them today
(package RK5).

## 1. Findings that change the plan (do these first)

**F1. The server stores more than the Raksha promise allows.** `CLAUDE.md` says the guidance-only Raksha "never accepts, stores or checks the images".
That is true only of the guidance reply. The same bot and web page also send any photo to the ordinary image checker, and `src/satyasutra/api/app.py` then keeps:

| Route | Stored in `data/reports.sqlite3` | Line |
|---|---|---|
| `POST /media/check` | the result, image metadata **and a thumbnail of the image** (`thumb_px: 220`) | `keep("media", {... "thumb": image_thumb(data, ...)})` |
| `POST /video/check` | the result summary | `keep("video", ...)` |
| `POST /voice/transcribe` | the **full transcript** | `keep("voice", out)` |
| `POST /notice/check` and document or page checks | the **text read from the image or page** | `keep("notice", ...)`, `keep(kind, out)` |
| the claim path | the claim text and verdict | `keep("claims", ...)` |

So a person who sends a private image to the bot, or describes what happened in a voice note, leaves a thumbnail or a transcript on our server with no way to
remove it. Telegram also keeps the chat on its own servers, which we cannot control and must say so. **This must be fixed before Raksha is shown to anyone**
(RK1, RK2). Until then the honest statement is: "the guidance route stores nothing; the image checker does".

**F2. The bot treats a private photo like any photo.** `bot/telegram.py` downloads every photo and posts it to `/media/check`. Nothing tells the bot the
person is in a Raksha situation.

**F3. Phrase lists are narrow.** `intimate_phrases` has about 25 English and 5 Hindi phrases. Real messages are Hinglish, short and indirect
("mera video viral kar dega", "he has my pics"). Missing the intent means the person gets a verdict-style reply instead of help.

**F4. Wording is unreviewed.** The Hindi routes and cards were written by us. For this feature a native reader must review every sentence.

**F5. Live scam warnings cannot hook the phone's call.** Neither Android nor iPhone lets an app read call audio. The only buildable version is
the person turning on **speaker and a listening page** (Scam Guard below). It must say so, and it must not claim to protect calls in general.

---

## 2. Raksha (guided help, never images)

### 2.1 Scope
- **Does:** recognise that someone is being threatened with, or has had, private images shared; ask a few short questions; branch for under 18; give the right next
  steps in their language; help them write down what they will report; point to StopNCII, the platform's report form, the cybercrime portal, 112.
- **Never does:** accept, open, hash, store, forward or analyse an intimate image; file or send a complaint; counsel; give legal advice; promise removal or
  anonymity; talk to the abuser; keep a record of the conversation.
- **Says plainly:** it cannot remove anything, cannot find every copy, and replaces neither a lawyer nor a counsellor.

### 2.2 Design
**Entry points.** (a) The bot detects the intent from a message (list from F3, with an "is this about private images?" question when unsure); (b) a `/help_me`
command (Hindi `/madad`); (c) a quiet web page `/help` (RK7).

**Flow (config-driven, Hindi and English, short messages, one question at a time).**
1. *Safety first.* "Are you in danger right now?" Yes: 112 and a trusted person, nothing else. No: continue.
2. *For whom.* "Is this happening to you, or are you helping someone?" (helping someone: gentler wording, never asks the person to forward anything).
3. *Age.* "Were you (or they) under 18 in these pictures?" Under 18 or unsure: **child route only**, no adult steps, no fingerprinting advice beyond what the
   official child-safety route says; unsure is treated as under 18.
4. *What is happening.* Being threatened / pictures already shared / pictures changed or faked. Each picks its own order of steps.
5. *Where.* Instagram, WhatsApp, Facebook, X, YouTube, Telegram, a website, "I don't know": the matching report route from config.
6. *Keep a record.* What to screenshot and note (profile name or number, links, dates, demands), why not to delete the chat, ask a trusted person to help.
7. *Write it down.* She types the usernames, links, dates and what was said. The bot returns a clean, dated **summary text** for her to copy into a report. No images,
   no legal section numbers, a line that she should check wording with a lawyer or a legal-aid service.
8. *What next.* Where to report (cybercrime portal, platform, StopNCII for adults), what not to do (pay, send more, delete), who to tell, 112 if unsafe.
9. *Close.* "I am not storing this chat." and how to clear the Telegram chat on her side.

**If an image arrives at any point:** the bot does not download it. Reply: "Please do not send the picture here. I cannot help with the image itself, and I do not
want to hold it. Keep it on your phone as a record. Here is what to do next:" and continue the flow.

**Tone.** Never asks why, never blames, never says "just". Short sentences. "It is not your fault." once, at the start, and not repeated.

### 2.3 Packages

| ID | Package | Hours | Files | Done when |
|---|---|---|---|---|
| **RK1** | **Private mode on the server.** Requests marked private (header from the bot and from `/help`) never write to `records` (no thumbnail, transcript or text), and `/act/plan` never logs the text. `keep()` refuses when a request is private. Server also refuses to store anything when the text matches the intimate intent, whatever the header says | 1.5 | `api/app.py` (`keep`), `records.py` | A test posts a photo, a voice note and a notice with the private header: **zero rows** written. A test sends intimate-intent text to `/claims`: zero rows |
| **RK2** | **The bot never takes a private image.** In-memory chat state with an expiry (no persistence): after the intent is detected or `/help_me`, photos, video and documents are refused before download; the bot sends the private header on every call it makes | 1.5 | `bot/telegram.py` | Tests with a fake Telegram: in help mode a photo is not downloaded and not posted anywhere; outside it, the existing photo path is unchanged but sends the private header |
| **RK3** | **The guided flow** in section 2.2 as config and a small state machine; Hindi and English; under-18 branch; platform picker with report links in config | 3 | `config/raksha.yaml`, `src/satyasutra/raksha/flow.py`, `bot/telegram.py` | A scripted conversation test walks every branch; the under-18 branch never shows an adult-only step; no step asks for an image |
| **RK4** | **Report summary generator.** Plain code template from the typed fields (usernames, links, dates, what was demanded); output is text only; no model call | 1 | `raksha/summary.py`, `config/raksha.yaml` | Fields in, dated text out; a test proves nothing outside the typed fields can appear in it |
| **RK5** | **Verify every route and number** on the official sites, record the date, and have a native speaker review each Hindi sentence. Check 112 and 1930; check the current women's and children's helplines before naming any; confirm the cybercrime portal categories; confirm StopNCII and Take It Down wording | 1.5 (people, not code) | `config/actions.yaml`, `config/raksha.yaml` | `confirmed_on_official_site: true` with a date for each route actually shown, or the route is removed |
| **RK6** | **Better recognition, measured.** Hinglish and indirect phrasing; a labelled test set of about 40 messages (20 that should trigger, including short and indirect ones; 20 that must not, such as film or news talk about "morphed" photos); report triggered share and false triggers with ranges. When unsure, ask, do not assume | 1 | `config/actions.yaml`, `tests/` | Numbers in the claim sheet; no non-trigger set message triggers without a question first |
| **RK7** | **Quiet web page `/help`**: the same flow, a quick-exit button that replaces the page, no history entry, no analytics, no cookies, private header on every call | 2 (stretch) | `ui/help.html` | Works on a phone width; the quick exit works; the server stores nothing (checked as in RK1) |
| **RK8** | **Forget.** A `/forget` command clears the in-memory chat state; an endpoint deletes a record by id for the web page | 0.5 (stretch) | `bot/telegram.py`, `api/app.py` | Test: state is gone after the command |

**Core** is RK1 to RK6 (9.5 hours). RK1 and RK2 (3 hours) are **not optional**: without them Raksha does not ship, and `CLAUDE.md` must not claim "never stores".

### 2.4 Demo and what may be said
- Demo only with text typed by a teammate and benign example wording. **Never** use a real or synthetic intimate image, even to show the refusal; show the refusal with
  an ordinary landscape photo while in help mode.
- Show the **proof of storing nothing**: the row count of the records table before and after a full help conversation (a 10-second command).
- Say: "guided help that never touches the image, tested on a labelled set of N messages, with every route confirmed on its official site on <date>".
- Do not say: it removes content, finds copies, is anonymous (Telegram sees the chat), gives legal advice, or "protects women". Do not quote statistics unless the team re-read them from the source.
- Legal text is out of scope. If a lawyer or legal-aid partner can look at the report-summary wording, say so; if not, say it was not reviewed.

---

## 3. Scam Guard (a warning while the call is happening)

### 3.1 What it is, honestly
A page the person opens **before or during a suspicious call**: one big button, "Is this call a scam?". They put the call on **speaker**; the page listens through the
phone's own microphone, transcribes short chunks, and watches for how scam calls are built. If the call looks like a scam it turns red, vibrates, speaks a short
warning in Hindi or English, and tells them what to do, in one line. Optionally it messages a family member.

It does **not** monitor calls in general, does not record, does not identify the caller, and cannot work if the person does not turn it on. Say all of that on the first screen.

### 3.2 Why script detection, not voice detection
Most scams in India are people reading a script (pretend police or customs officers, "KYC update", electricity cut-off, "your son is in trouble"). The signals are
in the **words and the structure**: an authority, a threat, urgency, secrecy, and a request for money, a code or remote access. A cloned-voice detector helps with one
kind of call and its current test is small and weak, so voice is a hint at most (SG8), never the trigger.

### 3.3 Detection design (plain code, no model call, config-driven)
Signals per chunk, each with word and phrase groups in English, Hindi and Hinglish, in `config/scam_script.yaml`:

| Signal | Examples of what it listens for (to be written and reviewed) |
|---|---|
| authority | police, CBI, customs, court, RBI, bank officer, "cyber cell", agency names |
| threat | arrest, case registered, account blocked, SIM disconnected, legal action |
| urgency | right now, within an hour, tonight, "do not disconnect" |
| secrecy | do not tell anyone, do not call your family, stay on this call |
| credential request | OTP, PIN, CVV, password, card number, UPI PIN |
| money request | transfer, "safe account", verification deposit, gift card, pay the fine |
| remote access | install an app, AnyDesk, TeamViewer, "share your screen" |
| personal crisis | accident, hospital, in custody, "send money now" (relative in trouble) |

**Rules (config, tuned on the evaluation set, not by feel):**
- A **hard signal** raises an alert alone: a credential request, a money request together with a threat or authority, or a remote-access request.
- Otherwise an alert needs **two different signals** inside a rolling window of chunks; signals decay if the call turns ordinary.
- **Negation and legitimate warnings are handled:** real banks say "never share your OTP". Phrases such as "do not share", "never ask you for" and their Hindi forms
  cancel a credential signal in the same sentence. This is the most important false-alarm guard and has its own test cases.
- Three states: *listening* (nothing), *be careful* (amber, one line), *this looks like a scam* (red, speak and vibrate).

### 3.4 Flow and architecture
1. `/guard` page (static, phone-first, large type, Hindi and English). First run: a short consent screen that says what it does, that it listens only while the button is
   on, that nothing is recorded or kept, and that the other person is not told. Mic permission is asked here.
2. The page records the microphone in chunks of about 6 seconds with a small overlap and posts each to `POST /guard/chunk` with a random session id.
3. The server decodes the chunk (ffmpeg), transcribes it with the **same GPU Whisper service** the voice notes use, runs the signal rules, updates the session state
   in memory, and returns `{signals, state, transcript_tail}`. **Nothing is written to disk or to `records`; sessions expire after minutes; audio is discarded after transcription.**
4. The page shows the state. On red: full-screen colour, `navigator.vibrate`, a spoken line with the browser's speech synthesis (a tap on the button satisfies the
   browser's rule about autoplay), and three large buttons: **Hang up**, **I already sent money / shared a code** (opens the 1930 and bank route that already exists),
   **Tell my family**.
5. **Family alert (opt-in, SG4).** In the bot, `/guard_link` shows a short code; entering it on the page links the phone to that Telegram chat. On red the server sends
   one message: "A call on <name>'s phone looks like a scam (type: <type>) at 17:42. Please call them. No audio or words were kept." Only the type and time are sent.

Latency target: first alert within about 10 to 15 seconds of the telltale sentence (one chunk plus transcription). **To be measured, not promised** (SG6).
If the GPU service is down the page says so: CPU transcription is slower than real time, so live warnings are not offered, and nothing pretends otherwise.

### 3.5 Packages

| ID | Package | Hours | Files | Done when |
|---|---|---|---|---|
| **SG1** | **Script rules.** `scam/script.py` (pure functions: normalise, detect signals, negation guard, rolling window, state) and `config/scam_script.yaml` with English, Hindi and Hinglish groups | 3 | `src/satyasutra/scam/script.py`, `config/scam_script.yaml` | Unit tests per signal and per state; negation cases pass ("do not share your OTP" does not alert); no numeric literal outside config |
| **SG2** | **`POST /guard/chunk`** with in-memory sessions, expiry, size limits, and a test that proves nothing is stored (no row in any database, no file) | 2 | `api/app.py` (integrator), `scam/session.py` | Test posts several chunks and asserts the data folder is unchanged; expired sessions are forgotten |
| **SG3** | **The page `/guard`**: consent, one big button, three states, vibration, spoken warning, the three action buttons, Hindi and English, readable at arm's length, works on a phone browser | 3 | `ui/guard.html` | Tried on a real phone and a desktop; the red state shows within one chunk of a scripted scam; the "already sent money" button opens the existing 1930 route |
| **SG4** | **Family alert** through the bot with a link code | 1.5 | `bot/telegram.py`, `api/app.py` | A second phone receives the message; the message contains no transcript |
| **SG5** | **Evaluation set and harness.** About 30 scam scripts across the types in 3.3 and about 30 ordinary calls, in English, Hindi and Hinglish (see 3.6); run the rules on the text; report detection rate by type, how many chunks until the alert, and the false-alarm rate on the ordinary set, each with a range | 2.5 | `tests/guard/`, `scripts/guard_eval.py`, `docs/claim-sheet.md` | The three numbers are in the claim sheet with their command; false alarms on the ordinary set are read and explained one by one |
| **SG6** | **End to end with real audio.** Speak or synthesise the scripts, play them through a phone speaker into the page, measure the time from the telltale sentence to the alert and the transcription error on Hindi and Hinglish | 1.5 | `scripts/guard_e2e.md` (procedure and results) | A table of 10 runs: latency, transcript quality, alert or miss |
| **SG7** | **Native-speaker review** of every Hindi and Hinglish phrase group and spoken warning, and a check that the warning sentences are plain and calm | 1 (people) | `config/scam_script.yaml` | Reviewer's name and date in the file's header |
| **SG8** | **Voice-clone hint** (stretch): run the existing synthetic-speech score on the same chunks and show "the voice may be synthetic" as an extra line when high; never alone, never red | 1 | `audio/spoof.py` use, `ui/guard.html` | Only appears with at least one script signal; stated as a weak hint |
| **SG9** | **After the call** (stretch): the existing `money_lost` and `scam_forward` routes opened from the Guard, with the scam types added to `actions.yaml` (digital arrest, KYC, parcel and customs, electricity, job and task scams) | 1 | `config/actions.yaml` | Each type returns the right routes and evidence pack |

**Core** is SG1 to SG3, SG5 and SG6 (12 hours); SG4 adds 1.5, SG7 is human time, SG8 and SG9 are stretch.

### 3.6 The evaluation set (how it is built, so the numbers mean something)
- **Scam scripts:** written by us from public descriptions of how each scam type runs (police and bank awareness pages, the national cyber-fraud portal's published guidance,
  press reports). Each file records the type, the language and **which public source described it**. No real victim's call is used.
- **Ordinary calls:** delivery, doctor's appointment, family chatter, a **real bank's fraud-warning call that says "never share your OTP"**, an electricity-office
  call that asks for nothing, a courier asking for an address, a genuine KYC reminder that asks you to visit a branch. These are the hard negatives.
- **Languages:** English, Hindi and Hinglish, with a native speaker writing or checking the Hindi and Hinglish.
- **Two splits as in the verdict dataset:** tune the thresholds on one third, report on the rest once. Report counts with Wilson ranges (the code is in `scripts/eval/lib.py` on branch `eval/verdict-dataset`, not yet merged; merge it first or copy the 20-line function).
- **What it cannot show:** real call audio quality, real accents, or how real scammers vary. It proves the rules work on the scripts we wrote, not on all calls. Say so.

### 3.7 Privacy, safety and law
- The page listens through the user's own phone only while the button is on, and discards audio after transcription. Nothing is stored server-side. Page memory holds
  the last few transcript lines on screen only.
- Recording or listening to a call, even through a speaker, has legal and ethical sides that differ by place and situation, and the other person is not told. We do
  not record and do not keep anything, and we **do not claim legal clearance**. If counsel cannot look at it before the event, say so. The consent screen is the user's choice and does not remove that question.
- False alarms frighten people and teach them to ignore warnings. The thresholds come from the evaluation set's false-alarm rate on ordinary calls, not from how impressive the demo looks.
- A missed scam is the other risk. The page must never say a call is safe: the highest-comfort state is "nothing alarming heard so far".
- Never present the system as protecting the user. It gives a warning and the next step; the person decides.

### 3.8 Demo
A teammate plays a "digital arrest" call from a second phone on speaker (the script comes from the evaluation set). The phone running `/guard` turns amber, then red inside
about two chunks, vibrates and speaks the warning; a third phone receives the family alert on Telegram with no transcript. Then the same page is left running through an
ordinary delivery call and **stays quiet**: show the false-alarm case on purpose. Close with the numbers from SG5 and the limits in 3.1.

---

## 4. Order, owners and checkpoints

Both features share the safety work, so it goes first. Times are targets from now; the checkpoints are fixed.

| When | Raksha | Scam Guard | Notes |
|---|---|---|---|
| Sat 18:00 to 20:00 | **RK1, RK2** (3 h, one session). Start RK5 verification (a person, in parallel) | **SG1** (3 h, another session) | RK1 and SG2 touch `api/app.py`: integrator applies, others send the patch |
| Sat 20:00 to 23:00 | **RK3** (3 h) | **SG2, SG5** (4.5 h split between two sessions) | |
| Sat 23:00 | **Checkpoint R1:** no private data is stored by any route (test passes); the bot refuses images in help mode | **Checkpoint G1:** rules pass their tests and the evaluation set is built | If R1 fails, Raksha is switched off, not shown |
| Sun 08:00 to 12:00 | **RK4, RK6** (2 h) | **SG3** (3 h), **SG4** (1.5 h) | Sleep before this; one person is always awake |
| Sun 12:00 to 14:00 | RK5 results merged; native-speaker review | **SG6** (1.5 h), SG7 review | |
| Sun 14:00 | **Checkpoint CP3 (feature freeze):** guided flow works end to end on benign text; the false-alarm number exists | | Anything not done is a roadmap slide |
| Sun 14:00 to 18:00 | stretch only (RK7, RK8, SG8, SG9) if ahead | | |
| Sun 18:00 to 22:00 | rehearse both demos twice | | |

**Cut order if time runs short:** SG9, SG8, RK8, RK7, SG4, RK6 (keep the recognition tests, drop the extra phrases), SG6. **Never cut:** RK1, RK2, SG1, SG2 (no-store test), SG5 (the
false-alarm number), the limits statements, and the native-speaker review of anything shown in Hindi (otherwise show English only).

**Owners.** Safety (RK1, RK2, SG2) and any change to `api/app.py` belong to the integrator machine's session. New modules (`raksha/`, `scam/script.py`, `ui/guard.html`,
`ui/help.html`, `config/raksha.yaml`, `config/scam_script.yaml`, evaluation files) can be built by any session on its own branch and merged by the integrator.

## 5. Things a person must do (no code can)
1. Open the official pages and confirm every route, number and category named in `actions.yaml` and `raksha.yaml`; record the date (RK5).
2. A native Hindi reader reviews all Hindi and Hinglish wording, spoken warnings included (RK5, SG7).
3. Decide who answers the "is it legal to listen on speaker?" question, and whether the demo shows Scam Guard to the public at all.
4. Test Scam Guard on at least two different phones in a quiet and a noisy room before it is shown.
5. Do not ask any real person to share real private images for testing, ever.

## 6. What this plan will and will not claim
- Claim: guided help that never touches images, with a proof that nothing is stored; a call warning that works on speaker with measured detection and false-alarm rates on a labelled script set.
- Do not claim: it protects every call, it works without the person turning it on, it removes content, it is legally cleared, it detects cloned voices, or any number not in the claim sheet.
