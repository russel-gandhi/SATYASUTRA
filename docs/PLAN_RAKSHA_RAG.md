# Build plan: Raksha, the grounded guide through the whole complaint path

Status: RR1 to RR5 and RR7 built on 2026-11-01, RR6 not built, RR8 first run only: see `docs/raksha-rag-sheet.md`. Written 2026-11-01. Extends `docs/PLAN_RAKSHA_SCAM_GUARD.md` (Raksha before this was a rules-based flow, `config/raksha.yaml`, `src/satyasutra/raksha/flow.py`).

## 1. What we are building

A person whose intimate images are threatened, leaked or faked gets guided from the first hour to the last resort:

1. **Safety and first steps** (the current flow, unchanged in spirit).
2. **Ask anything** about the process, answered only from official documents, each answer quoting its source.
3. **An escalation ladder**: file, wait, follow up, who to approach when nobody answers, what to do when still nothing happens.
4. **Drafts** of the complaint, the follow-up and the escalation letter, in English and Hindi, for the person to send themselves.
5. **Raising your voice safely**: how to use social media as pressure without exposing the victim, the images or yourself to harm.

Not goals: filing anything on a portal for the person (D42), accepting or checking images, giving legal advice, promising an outcome.

## 2. Principles that carry over from the rest of the project

- The model labels and phrases; plain code and curated data decide. A contact, a deadline or a legal provision is **never** produced from the model's memory. It comes from a retrieved official passage or a curated, sourced config entry.
- "I can't find this in the official documents" is a valid answer, exactly like CANNOT TELL YET. Out-of-corpus questions are refused, not guessed.
- Every quote the model returns must appear verbatim in a retrieved passage (same rule as claim extraction). If it does not, the answer is dropped.
- No images in, nothing stored on our server, sessions in memory only.
- Hindi wording and legal wording are marked unreviewed until a native speaker and a lawyer have reviewed them (`reviewed_by` / `reviewed_on` in config stay empty until then).
- No mock or hardcoded data in `src/`: the corpus is data on disk with a manifest; values live in `config/`.

## 3. What we found today about sources (probed 2026-11-01 with our honest User-Agent)

| Host | Result | Use |
|---|---|---|
| i4c.mha.gov.in | 200 | I4C pages, advisories (no robots.txt: 404) |
| cybercrime.gov.in | 200, but an Angular page: the text is rendered by JavaScript, `robots.txt` returns the app shell | Needs a rendered fetch or manual copy of the help and FAQ text; terms not read |
| gac.gov.in (Grievance Appellate Committee) | 200, with PDF attachments | How to appeal against a platform's decision |
| pgportal.gov.in (CPGRAMS) | 200 | Public grievance escalation |
| nalsa.gov.in | 200, robots allows everything but `/wp-admin/` | Free legal aid |
| delhipolice.gov.in | 200 | Example of a state cyber cell page; each state differs |
| sancharsaathi.gov.in | 200 | Chakshu and phone-number reporting |
| stopncii.org, takeitdown.ncmec.org | 200 | Hash-based takedown services (not Indian government) |
| indiacode.nic.in, meity.gov.in, mha.gov.in, legislative.gov.in | **403** | The statutes and IT Rules PDFs we most need: **blocked to us** |
| ncw.nic.in, egazette.gov.in, wcd.nic.in, cyber.gov.in | no answer | Not reachable |

Consequence: the statutes and the IT Rules 2021 text cannot be fetched by our bot. We do not disguise the client to get past the block. **The person running the project downloads those PDFs by hand** (official sites, in a browser) into `data/raksha_corpus/`, and the manifest records where and when each came from. Everything else above can be fetched.

## 4. Architecture

```
data/raksha_corpus/            official PDFs/HTML saved with a manifest (source URL, retrieved on, sha256, page)
        |  ingest (RR1)        split by section/page, keep page and heading as the citation
        v
data/raksha.sqlite3            FTS5 + embeddings (same pattern as the RBI/SEBI index)
        |  retrieve (RR2)      hybrid: keyword + embedding, top-k with scores, a floor below which nothing is returned
        v
answer (RR3)                   Gemini: answer ONLY from passages, return quote ids; code checks each quote is verbatim
        |
escalation ladder (RR4)        config/escalation.yaml: rungs, who, how, wait, what to attach, source passage ids
        |
drafts (RR5)                   templates filled from typed fields only (as the report summary is today)
        |
voice section (RR6)            curated, sourced, lawyer-reviewed text; retrieval only for its citations
        |
bot + web (RR7)                /ask, /ladder, /draft, /where-am-i in Telegram; same on the web page
```

Raksha's existing flow stays the front door: safety, whom, age, what, where, details. Its end result now also names the rung the person is on and offers the next questions.

## 5. Packages

Each package ends when its Done-when command passes. Hindi and legal review are separate people-tasks (section 8), not code.

**RR1: Corpus and ingest.**
Manifest schema (strict pydantic, extra=forbid): `id, title, publisher, url, retrieved_on, sha256, kind (statute|rules|advisory|portal_help|service)`. Ingest splits by heading or page, keeps the page number, rejects a file whose hash differs from its manifest. Fetch what answers (gac.gov.in PDFs, I4C, CPGRAMS, NALSA, a rendered copy of the cybercrime.gov.in help text) with the honest User-Agent and a host gate; the hand-downloaded PDFs are added to the manifest.
Done when: the index builds from the manifest, a changed byte in a PDF fails the build, and `scripts/raksha_corpus.py list` prints every document with its hash and page count.

**RR2: Retrieval.**
Hybrid search with a relevance floor; English and Hindi questions (Hindi questions are translated to English for retrieval, as the claim agent does, and the Hindi original is kept).
Done when: on a labelled set of at least 40 questions each with its expected source passage, the right passage is in the top 3 for a stated share of them, and the number is reported with its sample size.

**RR3: Grounded answers with refusals.**
Prompt in `config/prompts/raksha_ask.txt`. Output schema: answer sentences, each with the passage id and a verbatim quote. Code drops any sentence whose quote is not verbatim in that passage; if nothing survives, the reply is the refusal text. The answer never names a deadline, fee, phone number or section that is not inside a quoted passage.
Done when: a test set of out-of-corpus questions (weather, a different crime, "will the police arrest him") is refused in full, and a test shows a fabricated quote is dropped.

**RR4: Escalation ladder.**
`config/escalation.yaml`, strict schema. Each rung: `id, who, how, what_to_say, attach, wait_before_next, next_if_no_reply, source_passage_ids`. Rungs, subject to what the official documents actually say: portal or local cyber cell complaint with acknowledgement number; nodal or grievance officer of the police unit; senior police officer for cyber crime in the district or state; the platform's grievance officer; the Grievance Appellate Committee; CPGRAMS; the National and State Commissions for Women; free legal aid; a court petition. The "wait" values come from the documents; where none is stated, the field says so and the bot says "no time limit is stated in the documents we have".
The bot keeps a small per-session record of the rung and the dates the person types, then answers "where am I" and "who next".
Done when: a test walks a session through every rung, a rung with no source passage fails config validation, and the ladder has no cycle or dead end.

**RR5: Drafts.**
Complaint, follow-up and escalation letters from typed fields only (name or alias, dates, links, acknowledgement number, rung), in English and Hindi, no model call. They carry the same "this is not legal advice, ask a lawyer or legal-aid service to check before you send" note.
Done when: a test fills each template from sample fields and shows no text outside the fields and the fixed wording; Hindi renders with the Noto Devanagari font on the card or PDF.

**RR6: Raising your voice safely.**
A curated, sourced section, not retrieval-generated: which official handles exist for cyber police and platform support (verified on their own pages on a stated date), what to post and what never to post (no images, no victim's name, no identifying details), why naming the accused carries legal risk, how to keep a dated record of every post and reply, and when to stop and take advice. A lawyer reviews this section before it ships.
Done when: every handle and claim in the section has a source URL and a "verified on" date, and a check refuses to build if one is missing.

**RR7: Bot and web.**
Telegram commands `/ask`, `/ladder`, `/draft`, `/where`; the same on a web page `/help`. Rate limits as for `/verify`; Gemini failure shows the existing "model busy" message and never a guess.
Done when: a scripted conversation through the bot (local API, no Telegram network) covers the flow, one grounded answer, one refusal, one draft and the ladder, and the tests pass.

**RR8: Evaluation and honest limits page.**
A labelled question set, split into dev and test, run once on the test split. Report: grounded-answer rate, correct-refusal rate on out-of-corpus questions, share of answers whose every quote is verbatim, and the cases it got wrong. Update `docs/raksha-sheet.md` and the about page with what was not tested.
Done when: the numbers are in the sheet with their sample sizes and the failing cases are listed, not hidden.

## 6. Safety checks that run in the tests

- No code path accepts an image or file from the person.
- A danger answer or the words for self-harm always return the 112 and trusted-person message first, before any retrieval.
- Replies never promise an outcome ("they will remove it", "the police will act").
- A minor (age under 18 or unsure) gets the routes and notes for minors, and the bot never shows the drafts' wording that would describe the images.
- Logs hold no message text and no keys; errors report the exception class only.

## 7. Evaluation questions (RR2, RR3, RR8)

Written with sources, not from memory: for each question the author records the document and page that answers it. Include Hindi questions, questions with no answer in the corpus, questions that tempt a guess (a deadline, a fee, a section number), and questions about other crimes. A second person labels part of the set.

## 8. People-tasks (not code)

- Download the statutes, the IT Rules 2021 and any MHA/I4C advisory PDFs by hand from the official sites into `data/raksha_corpus/` (those hosts block our bot).
- Read each source's terms of use before we index it; record the result in the manifest.
- A native Hindi reviewer and a lawyer or a legal-aid volunteer review the wording, the ladder and the voice section.
- Confirm the routes on the official sites in a browser and set `confirmed_on_official_site` only after that.
- Decide the model and quota for embeddings and answers (free tier is 15 requests a minute per model).

## 9. Risks we state up front

- Without the statute PDFs the bot can answer the process questions but not legal-provision questions; it must say so.
- The cybercrime portal text is rendered by JavaScript and its terms are unread.
- Portals, helplines and handles change; each entry carries a "verified on" date and the bot shows it.
- A kind official system still does not guarantee a first-try fix. The bot never says it will. It states the next rung and the order to try them in.
- The voice section is the highest legal-risk part and ships only after review.

## 10. Decisions needed from you

1. Do you download the statute and rules PDFs by hand, or should we work only with what our bot can fetch for now?
2. Which state or states first (each state's cyber cell and escalation officers differ)?
3. Can a native Hindi speaker and a lawyer review the text before any real use?
