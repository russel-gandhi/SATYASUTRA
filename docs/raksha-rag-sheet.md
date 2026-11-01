# Raksha grounded guide: what was built and what was measured (2026-11-01)

Plan: `docs/PLAN_RAKSHA_RAG.md`. This sheet says what exists, the numbers from one live run, and what was **not** done or tested.

## What exists

| Plan package | State | Where |
|---|---|---|
| RR1 corpus and ingest | built | `src/satyasutra/raksha/corpus.py`, `config/raksha_rag.yaml`, `scripts/raksha_corpus.py` (`fetch`, `add`, `build`, `list`, `peek`). Manifest with SHA-256; a changed byte fails the build. |
| RR2 retrieval | built | keyword search (FTS5, prefix) plus a coverage floor (`min_coverage` 0.5, a policy choice). No embeddings. |
| RR3 grounded answers, refusals | built | `ask.py`, `config/prompts/raksha_ask.txt`. Quote must be verbatim in the cited passage, every figure (digits, Devanagari numerals, number words) must be in the quote, promises are dropped, nothing left means refusal. Danger words give the 112 text before any retrieval or model call. |
| RR4 escalation ladder | built (Delhi) | `config/escalation.yaml`, `ladder.py`, `GET /raksha/ladder`. Eight rungs, every rung with quoted sources that are re-checked against the index on each call. |
| RR5 drafts | built | `config/raksha_drafts.yaml`, `drafts.py`, `POST /raksha/draft`. Typed fields and fixed wording only, English and Hindi, no model, no law cited, minor wording does not describe the images. |
| RR6 raising your voice safely | **not built** | the highest legal risk and no reviewer is available. |
| RR7 bot and web | built | web: `/help` (ladder, ask, draft added); Telegram: `/ladder`, `/ask`, `/draft`. `/where` (a saved position) is not built: the page and bot hold no state by design. |
| RR8 evaluation | first run only | below. |

## Corpus (15 documents, fetched 2026-11-01 with our honest User-Agent)

GAC (home, FAQ, two attachments), CPGRAMS, NALSA (home, victim compensation), I4C (home, NCRP, FAQ, help, acts and rules), Delhi Police (home, contacts, services),
Sanchar Saathi, StopNCII.org, Take It Down. Statute hosts (indiacode, meity, mha, legislative) answer 403 to our client and are not fetched; **the IT Rules and the criminal
laws are not in the corpus**, so the guide cannot answer provisions, deadlines of a platform's grievance officer or penalties, and says so. `cybercrime.gov.in` is rendered by
JavaScript and is not indexed (its facts come through the I4C pages). Publishers' terms of use were **not read** (`terms_read: false` in the manifest). The corpus folder is
git-ignored: rebuild it with `fetch` then `build`; PDFs downloaded by hand go in with `add`.

## First live run (`docs/calibration/raksha_rag_2026-11-01.txt`)

25 questions by one labeller (14 answerable from the documents, 11 not; two in Hindi each way), one run, no held-out split:

- retrieval: an expected document in the retrieved passages for 14/14
- grounded answer: 12/14; correct (an expected word in an answer sentence): 12/14
- refused when no document answers: 11/11 (including "which section punishes...", "how many hours must a platform take", "will the police arrest", "how much compensation")
- misses (both over-refusals, the safe direction): "Who is on the Grievance Appellate Committee?" and the Hindi question about checking complaint status. The Hindi question was answered in an earlier manual run, so model output varies between runs.
- "every quote verbatim" is enforced by code on every sentence shown; it is not a measured rate.

Small sample, one labeller, expected words chosen by the same person: treat as a smoke test with numbers, not an accuracy claim. Not tested: questions written by someone else,
real users, colloquial or Hinglish phrasing, longer multi-part questions, other states.

## What the guide does not do

- It never accepts, stores or checks an image, files nothing, and promises no outcome.
- The ladder is Delhi only; rungs for women's commissions and a court petition are absent because no document we hold covers them.
- The Hindi wording (ladder, drafts, interface) is ours and **unreviewed by a native speaker or a lawyer**; `reviewed_by` stays empty and the page says so.
- The `senior_officer` rung rests on a contacts page that lists officers; the documents do not say it is a required step, and the rung says that.
- The "further queries" sentence the `police_station` rung quotes sits in I4C's answer about the 1930 helpline; the rung says so.
- Retrieval keeps a passage when it holds half of the question's words, so an out-of-scope question can still retrieve passages (the weather question retrieved 3); the model then refuses. The refusal for those cases rests on the model returning no sentence, not on code alone.

## People-tasks still open

Download the statute and IT Rules PDFs by hand and `add` them; read each publisher's terms; a Hindi reviewer and a lawyer for the wording and the ladder; confirm the routes on
the official sites in a browser; a second labeller for the question set; more states.
