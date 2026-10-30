# Raksha build map: help for victims of intimate-image abuse, built so that we cannot see the images

Instructions for whoever builds the `raksha` module (lane 5). Raksha helps a person whose intimate images have been leaked or
threatened: it prepares takedown requests and a police complaint, tracks the case, and routes to human support. The rule that
shapes every decision below is **the operators of this system must be unable to view the victim's images, even if they wanted to.**
The reasoning and threat model are in [Bible Part 19](BIBLE.md#part-19-raksha-intimate-image-abuse-support).

Each phase lists files, steps and a **done when** check. Hours are guesses. Legal references below are from general knowledge and
**have not been verified by us**; a lawyer or the organisers' legal contact must check every section number, deadline and form
name before they appear in the product.

## 1. What it is, and what it is not

| It is | It is not |
|---|---|
| A guided case builder: hashes on the device, an evidence pack encrypted on the device, ready-to-send takedown and complaint text | An auto-filer that logs in to government portals or defeats CAPTCHAs. We will not build that |
| Hash-only: the server receives fingerprints and case status, never the images | A place that stores, views, reviews or classifies the victim's images |
| A tracker with reminders and escalation steps | A promise that content will be removed. We say "prepared and tracked" |
| A router to human help: helplines, legal aid, counselling | A replacement for any of them |

**On "automated filing".** As far as I know the national cybercrime portal has no public submission API and requires login and
OTP, so full automation is not available to us. Day-0 task: ask the organisers whether any official intake channel exists. The
demo shows a complete submission pack and a guided hand-off, and says so.

## 2. Architecture

```
 VICTIM'S DEVICE (browser, WebAssembly + WebCrypto)                SERVER (hash-only)
 ┌──────────────────────────────────────────────────┐           ┌─────────────────────────────┐
 │ choose image(s) ─► fingerprint (pHash, PDQ, both │  hashes   │ case row: id, status, created,│
 │ orientations) ─► never uploaded                   │ ────────► │ expiry, hash list, platform   │
 │ screenshots, URLs, notes ─► evidence pack         │  only     │ checklist state               │
 │ encrypt with a key derived from a passphrase      │           │ NO images, NO plaintext notes │
 │ that only the victim knows ─► download / share    │           │ NO phone number by default    │
 └──────────────────────────────────────────────────┘           └─────────────────────────────┘
        │                                                          │
        └── takedown text, complaint text, helpline list (generated in the browser from config templates)
```

Key choices:
1. **The server never receives raw media.** Fingerprinting and encryption happen in the browser. A test (RK-2) intercepts the
   network and asserts that no request body contains image bytes.
2. **The evidence pack is encrypted with a key we never get.** Derive it from a passphrase chosen by the victim (a slow key
   derivation function, parameters in config), or from a device-held key. If they lose it, we cannot recover it, and the UI says so
   before they create the case.
3. **Case rows hold the minimum:** a random case id, status, creation and expiry times, the hash list, and the checklist state. No
   name, phone or email unless the victim opts in to reminders; a contact, if given, is encrypted and expires with the case.
4. **Expiry is automatic** (default in config) and **deletion is one action**, with a deletion receipt that lists what was
   removed.
5. **Logs never contain hashes or case ids together with IP addresses.** Access logs for these routes are disabled or coarse.
6. **Operators have no read path.** The database role used by the application cannot select the evidence pack, because it does
   not exist server-side. The only operator-visible data is aggregate counts.

## 3. Hard stops

| Situation | Behaviour |
|---|---|
| Anyone may be under 18 | The flow stops before any hashing. Show child-protection routing (the national child helpline and the authority named in config) and explain why. Nothing is hashed, stored or processed |
| The person says someone else is in the image | Explain that only the person pictured, or a guardian or lawyer acting for them, should file; offer the support list. No hashing for third parties |
| Threat to life or immediate danger | Show emergency numbers first, before anything else |
| The user wants us to look at the image | Say plainly that we cannot and will not; describe what we do instead |

## 4. Phases

### RK-1: Threat model, retention and consent copy (lane 5, about 2 h)
Files: `docs/raksha_threat_model.md`, `config/raksha.yaml` (retention, expiry, helpline list, platform list), `docs/raksha_copy.md`.
1. Copy the Bible Part 19 abuse cases into a table with a test for each.
2. Write the consent and "what we store" screen text in plain language, in at least the languages the bot supports. Have a
   native speaker read each version.
3. Fill the helpline and authority list in config from official pages **read during the build**, each with a source URL and the
   date checked. Do not type numbers from memory.
4. Decide retention (Bible Q11 becomes blocking for this module).

Done when: the threat-model table exists with a test name per row, and `config/raksha.yaml` loads with every helpline carrying
a source URL and a checked date.

### RK-2: On-device fingerprint and encrypted evidence pack (lane 5, about 5 h)
Files: `ui/raksha/hash.worker.js`, `ui/raksha/pack.js`, `ui/raksha/crypto.js`, `tests/e2e/test_raksha_network.py`.
1. **Fingerprint in a web worker.** Decode the image on a canvas, compute pHash and the PDQ-style hash for the image and its
   horizontal flip. Do not use a server round trip. If a published JavaScript or WebAssembly build of PDQ is not suitable, use
   pHash only and say so in the claim sheet. Check any library's licence first.
2. **Evidence pack.** The user adds screenshots, URLs where the content appears, dates seen and notes. The pack is a single
   archive containing those items and a manifest of SHA-256 hashes and capture times.
3. **Encrypt** the pack with WebCrypto (AES-GCM) using a key from the passphrase via PBKDF2 or a stronger derivation available in
   the browser; parameters in config; random salt and nonce stored with the ciphertext. The passphrase and key never leave the
   browser.
4. **Network test.** An end-to-end test drives the page with a real test image, records all network requests, and fails if any
   request body or URL contains image bytes, the passphrase or plaintext notes.
5. **Test image source.** Use a benign photo that a consenting teammate took themselves. Never use real or synthetic intimate
   content for development or the demo.

Done when: the network test passes, the pack decrypts with the right passphrase and fails with a wrong one, and a flipped copy of
the test image yields a matching hash.

### RK-3: Hash-only case store (lane 5, about 2 h)
Files: `raksha/store.py`, `raksha/api.py`, `tests/unit/test_raksha_store.py`.
1. Table `cases(id, status, created_at, expires_at, checklist jsonb)` and `case_hashes(case_id, kind, value)`. No other columns.
2. Endpoints: create case, add hashes, update checklist, delete case (returns a receipt), and an expiry job.
3. The ledger records only `case_created`, `case_deleted` and counts, never hashes or content.
4. Tests: no column can hold free text longer than a status code; deletion removes hashes; expiry removes expired cases; an
   operator-role database user cannot read anything that identifies a person.

Done when: deleting a case removes every row that referenced it and returns a receipt, and the expiry job removes a test case.

### RK-4: Takedown and complaint pack generator (lane 5, about 3 h)
Files: `raksha/templates/*.md`, `raksha/pack_text.py`, `config/raksha.yaml`.
1. Templates per destination, in the user's language: platform abuse form text, search-engine removal request text, the
   intermediary's grievance officer, and the police complaint text. Each template has slots for URLs and dates the user entered.
2. The text is assembled **in the browser** from the template and the user's entries, so entries never reach the server.
3. Each destination in config has an official page URL, the date we checked it, and the notes a person needs (what the form asks
   for, how long a response is expected). Every legal reference in a template cites its source and carries a "verify with
   counsel" marker until the legal reviewer signs it off, tracked in `docs/raksha_legal_review.md`.
4. A "send yourself a copy" option exports the text without any server involvement.

Done when: a test case generates text for each destination in two languages, with every slot filled from the entries and none
empty, and every legal citation in a template appears in the review file.

### RK-5: Guided flow, support routing and hard stops (lane 5, about 2 h)
Files: `ui/raksha/flow.js`, `bots/raksha_flow.py`.
1. Steps: safety check, what we store, hash the images, add evidence, review text, how to file with the official portal and the
   platforms (step by step, with the official links from config), checklist.
2. Implement the hard stops of section 3 as the first screen, with tests that each one stops the flow.
3. The Telegram bot gives the same entry point but **never asks for the image in chat**. It sends the link to the web flow,
   because a chat bot would receive the image bytes.
4. Offer human support at every step: helplines, legal aid and counselling from config.

Done when: each hard-stop test passes, and the bot reply contains the link and no request for an image.

### RK-6: Spread scan (Tier 2, about 3 h)
For hashes the user chooses, run reverse search on the user's own device-selected preview only if they opt in, or compare against
public matches the user pastes as URLs. The server must still never receive the image: any reverse search of the image itself
goes from the user's browser through the user's own request, not through our server. If that is not possible, skip this phase
and say so. Done when the privacy network test still passes with this feature on.

### RK-7: Case tracker and reminders (Tier 2, about 3 h)
Checklist states per destination (prepared, sent, acknowledged, deadline passed, escalated), plus reminders to the opted-in
contact. Escalation text for the grievance officer and the appellate route, from config. Done when a deadline in a test case
triggers a reminder and the escalation text.

### RK-8: Platform hash-matching programs (Tier 3, depends on access)
If a recognised hash-sharing programme will accept submissions from us, integrate it. That needs an agreement we may not get
within the event. Check on day 0. Do not claim integration without a written yes.

### RK-9: On-device deepfake check (Tier 3, about 6 h)
Run a small image classifier in the browser (ONNX Runtime Web) so a victim can learn whether an image is likely synthetic,
without upload. Report it as a clue only. Done when it runs offline with the network test passing.

## 5. Abuse cases and tests

| Abuse | Defence | Test |
|---|---|---|
| Someone hashes another person's image to trigger takedowns | We never trigger takedowns ourselves; the pack goes to the victim, who files; hard stop for third parties; per-device case limits from config | Limit test; flow test |
| An operator tries to read a victim's data | No image or plaintext on the server; the application role cannot select identifying data | Role-permission test |
| A database dump leaks | The dump holds hashes and statuses only | Dump inspection test |
| A log leaks | No hashes with IPs; coarse access logs | Log scan test |
| Phishing clone of the page | The page states its address; guidance to check it; no passwords collected | Copy review |
| A minor uses the flow | Hard stop before hashing | Flow test |
| A forced or coerced user | Quick-exit button and no residue in browser history beyond config | Manual test |
| Retention creep | Expiry job and deletion receipt | Expiry test |

## 6. What we do not claim

- That content will be removed, or by when.
- That the legal references are correct until counsel has signed them off.
- That hash matching finds every copy: edits, crops and re-encodes can defeat it, and we report match rates by condition only for
  images we are allowed to test with.
- That this is anonymous against a network observer or a device compromised by someone close to the victim.

## 7. Pitfalls

- Letting a debugging tool, error tracker or analytics script see the page contents.
- Using a real or synthetic intimate image in development, screenshots or the demo.
- Typing helpline numbers or legal sections from memory.
- Sending the passphrase, or a hash of it, to the server.
- A Telegram flow that asks for the image.
- Reminders that leak the topic in the message text (use neutral wording).
- Promising removal or anonymity.
