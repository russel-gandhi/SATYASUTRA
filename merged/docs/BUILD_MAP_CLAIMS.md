# Claims build map: retrieval, evidence checking and verdicts

Instructions for whoever writes the `claims`, `agent` and `gate` modules. Follow the phases in order. Each phase lists files,
steps and a **done when** check. Time figures are guesses. Anything marked _measure_ must come from a script.

**Merged-plan changes.** This map covers the **claim axis** only; media verdicts come from the
[provenance map](BUILD_MAP_PROVENANCE.md) and the [forensics map](BUILD_MAP_FORENSICS.md). Compared with the original: claim labels
are `SUPPORTED`, `REFUTED`, `MISSING_CONTEXT`, `UNVERIFIABLE_YET`, `NOT_A_CLAIM` (Bible D31); sources carry a trust tier 1 to 4 and
`SUPPORTED`/`REFUTED` need Tier 1 or 2 (D35); screenshots are read with OCR or a vision model before extraction (D37); the agent
is a LangGraph state graph that streams a live trace (D32); `UNVERIFIABLE_YET` items are re-checked on a schedule (D38).

## 1. Where this sits

```
 post ─► language + normalise ─► check-worthy? ─► atomic claims ─┐
                                                                 ▼
   AGENT (plans, retries, re-plans, behind the GATE)  ◄── claim, budget
      │  picks sources by topic
      ▼
   fetch live ─► chunk ─► index ─► retrieve (BM25 + dense) ─► top-k passages
                                                                 │
                                   VERIFIER (MiniCheck): support probability per passage, plus the exact chunk used
                                   CONTRADICTION: fact-check rating, or an NLI check against official text
                                                                 │
                                   AGGREGATE (rules, config thresholds) ─► verdict + Evidence records
                                                                 │
                                   JUSTIFY (constrained) ─► GUARD: every sentence must pass the verifier ─► ledger
```

## 2. The resources, and what each is for

| Resource | What it really is | Role in our build | Constraint |
|---|---|---|---|
| **MiniCheck** (code; models Flan-T5-Large, DeBERTa-v3-Large, RoBERTa-Large) | Takes a document and one sentence, returns the probability the document **supports** the sentence | The verifier. Default model: Flan-T5-Large (770M). DeBERTa and RoBERTa are smaller and faster alternatives to compare | It answers *supported or not supported*. **It cannot say "refuted".** Unsupported is not false. It is sentence-level, so split claims first |
| **Bespoke-MiniCheck-7B** | The strongest model in the family, 7B | Optional upgrade if you have a big GPU | Weights are CC BY-NC 4.0, with commercial use by contacting the authors. The package's vLLM route is Linux only |
| **HerO** (pipeline, AVeriTeC 2024) | Retrieval, question generation, fine-tuned LLM verdicts, built for web evidence | A **design reference**: hypothetical-document query expansion, two-stage retrieval, verifying questions, verdict with justification | GitHub shows no licence. Its README says evidence retrieval takes about 6 hours on two H100s. It is not a thing to run on a laptop |
| **HerO 2** (pipeline, AVeriTeC 2025) | Lighter version: dense retrieval, per-passage summaries, answer reformulation, a quantised 32B verdict model | A design reference for the same ideas, with summarisation added | Code is CC BY-NC 4.0; the verdict model is large |

Licences, as shown on the sites when I checked: MiniCheck code Apache-2.0; the three small MiniCheck models MIT on Hugging
Face; Bespoke-MiniCheck-7B CC BY-NC 4.0; HerO code none shown; HerO 2 code CC BY-NC 4.0; HerO's 8B model checkpoint CC BY-NC
4.0. Re-check each page before you rely on it. Non-commercial terms are fine for an event but matter if the project continues.

**The event rule.** Reusing open-source libraries is allowed; core logic, models and agent workflows must be integrated and
built during the event window. Pretrained MiniCheck is an integration. What you build in the window is everything around it:
retrieval, decomposition, source adapters, the agent, the contradiction logic, the calibration, the guard and the evaluation.
Keep the verifier behind an interface so it can be swapped, and ask the organisers once, in writing, whether using pretrained
open models inside the pipeline is acceptable.

## 3. Design decisions, stated plainly

1. **Unsupported is not refuted.** `SUPPORTED` needs a supporting passage. `REFUTED` needs a *positive* reason: a trusted
   fact-check rated the matching claim false, or a contradiction check on an official passage fired. Otherwise the answer is
   `UNVERIFIABLE_YET`.
2. **Every claim is one self-contained sentence** before it reaches the verifier: pronouns resolved, date and place added.
3. **The cited passage is the chunk the verifier actually used.** MiniCheck's `score` returns the chunk behind the highest
   support probability, so the citation is the evidence itself, not a model's recollection of it.
4. **Verify the verifier.** Any generated justification is split into sentences, and each must be supported by the passage it
   cites. A sentence that fails is removed. If nothing survives, the verdict stands with no prose.
5. **The default threshold of 0.5 is a starting point.** Calibrate on our own labelled set and store the value, with its model
   hash and date, in `config/thresholds.yaml`.
6. **Language.** The MiniCheck base models are English models (check each model card). Indian-language claims and passages are
   translated first; record the translation model, show a "translated" mark in the UI, and report accuracy per language.
7. **Cache by content.** Key every verifier call by `(claim_hash, passage_hash, model_version)` so repeated work costs nothing and
   the ledger stays idempotent.

## 4. Contracts

```python
@dataclass(frozen=True)
class Passage:
    source_id: str; url: str; fetched_at: datetime
    text: str; sha256: str; tier: Literal[1, 2, 3, 4]   # 1 official, 2 fact-checker or wire, 3 news, 4 other; list in config
    language: str; translated_from: str | None

@dataclass(frozen=True)
class SupportResult:
    passage: Passage
    support_prob: float            # verifier output
    used_chunk: str                # the exact text that earned the score
    model: str; model_sha256: str

@dataclass(frozen=True)
class Evidence:                    # what the ledger and UI store; mirrors SupportResult plus role
    role: Literal["supports", "contradicts", "context"]
    result: SupportResult | None; factcheck: dict | None

class Verifier(Protocol):
    def score(self, passages: list[Passage], claim: str) -> list[SupportResult]: ...
```

`Verdict.evidence` must be non-empty for any verdict other than `UNVERIFIABLE_YET`; the constructor enforces it.

## 5. Phases

### C0: Day-0 setup (about 3 h)
1. Create the environment and install the verifier package:
   `pip install "minicheck @ git+https://github.com/Liyan06/MiniCheck.git@main"`.
2. Download the three small models once into a cache directory set in config (`cache_dir`), so the demo never waits on a download.
3. Run the README example (a document, one true claim, one false claim) and confirm the labels come out `[1, 0]`. This is the
   smoke test.
4. Record the GPU, memory and OS in `docs/hardware.md`. Do not assume a GPU. Measure CPU speed in C1.
5. Get the keys for the Google Fact Check Tools API and any source you will use; keys go in environment variables.
6. Download the evaluation sets: **LLM-AggreFact** (`load_dataset("lytang/LLM-AggreFact")`) for the verifier, and **AVeriTeC**
   for claim-verification checks in English.
7. Send the organisers the question about pretrained models (section 2). Save their reply in `docs/`.

### C1: Verifier adapter (about 3 h)
Files: `claims/verify/minicheck_adapter.py`, `tests/unit/test_verifier.py`, `scripts/bench_verifier.py`.
1. Wrap `MiniCheck(model_name=..., cache_dir=...)` behind the `Verifier` protocol. Model name comes from config.
2. Call `scorer.score(docs=[...], claims=[...])`. It returns `(pred_label, max_support_prob, used_chunk, support_prob_per_chunk)`.
   Use `max_support_prob` and `used_chunk`; ignore `pred_label`, because the 0.5 cut-off lives in config.
3. Batch: pass many passages for the same claim in one call.
4. Cache results in Postgres by the key from section 3, point 7.
5. Do not paste a claim of several sentences into it. Split first (C5).
6. `bench_verifier.py`: time a batch of passages for each of the three small models, on this machine, and write the table to
   `docs/verifier_bench.md`.
7. Unit tests, which may use small fixture strings: supported pair scores above unsupported pair; same input twice hits the cache
   and runs the model once; a very long passage returns a chunk, not an error.

Done when: the tests pass and `docs/verifier_bench.md` shows seconds per passage for each model.

### C2: Choose the model and the threshold with data (about 4 h)
Files: `scripts/eval_verifier.py`, `scripts/calibrate_threshold.py`.
1. Run each small model over a stratified sample of LLM-AggreFact; report balanced accuracy overall and per sub-dataset.
2. Plot balanced accuracy against seconds per passage. Choose by that plot, not by the leaderboard.
3. Calibrate: fit a threshold (or Platt scaling) on one split and report on another. Write the threshold, the model hash, the
   date and the data used to `thresholds.yaml`.
4. Keep a second, **grey band**: probabilities between a low and a high cut-off are "uncertain", which the agent uses to decide
   whether to look for more evidence (C8).
5. This benchmark has model-written claims checked against a supplied document. Our task differs (human claims, retrieved
   documents), so these numbers say only that the verifier works. They are not the system's accuracy.

Done when: `thresholds.yaml` is regenerated by the script and `docs/verifier_choice.md` explains the choice with the plot.

### C3: Live sources, chunking and index (about 6 h)
Files: `ingest/sources/<name>.py`, `claims/index/chunk.py`, `claims/index/store.py`.
1. One adapter per source, all implementing `fetch(query) -> list[RawDoc]`. URL, key variable name, rate limit and cache time
   come from `sources.yaml`. Adapters raise `Unreachable`, `RateLimited` or `SchemaChanged`, never return an empty list silently.
2. Strip navigation and boilerplate; keep the main text, title, date and URL.
3. Chunk into passages of a few sentences with overlap. Keep the chunk size in config, and make it about the verifier's own
   chunk size so one passage is not split again (MiniCheck splits at roughly 400 to 500 tokens, by model).
4. Index with BM25 (a keyword index) and dense vectors in Qdrant. Every record keeps `source_id`, `url`, `fetched_at`, `tier`.
5. A time-to-live per source; stale passages are refetched. This is a cache of live data, not a stored dataset.

Done when: `scripts/probe_sources.py` shows each configured adapter returning real documents and the index contains passages with
timestamps from today.

### C4: Retrieval (about 6 h)
Files: `claims/retrieve/bm25.py`, `dense.py`, `hybrid.py`, `hyde.py`, `scripts/eval_retrieval.py`.
1. **Stage one:** BM25 and dense retrieval, merged (reciprocal rank fusion). HerO uses BM25 then a dense reranker; HerO 2 uses
   a dense retriever. Choose the dense model by measurement. For Indian-language text, test a multilingual embedding model as well
   as an English one (check the model card for languages).
2. **Stage two:** rerank the merged top candidates with a cross-encoder or the dense scores, to a small `k` from config.
3. **Query expansion (optional, measured):** ask an LLM to write a short hypothetical fact-checking passage for the claim, and
   search with it as well. Keep it only if the ablation shows a gain.
4. **Build the retrieval test set from live data:** pull recent reviewed claims from the Fact Check Tools API, with their ratings.
   **Exclude the reviewing article itself, and its whole domain, from the index during the test.** Otherwise the system reads the
   answer. Write a test that fails if an excluded URL appears in any result.
5. Report recall@k for k in config, per source tier and per language, with and without expansion.

Done when: `eval_retrieval.py` prints recall@k tables and the leakage test passes.

### C5: Claim extraction and decontextualisation (about 5 h)
Files: `claims/extract/checkworthy.py`, `decompose.py`, `normalise.py`.
1. Detect the language and normalise the text: Unicode form, Roman-script Hindi detected and transliterated or translated.
2. Check-worthiness: a classifier or an LLM prompt that keeps factual, specific, verifiable statements and drops opinion,
   questions and greetings. Keep the rejected sentences in the output with a reason, for the UI.
3. Decompose into atomic claims, one sentence each. Make each self-contained: replace pronouns, add the date and place from the
   post's metadata. Keep a pointer from each claim back to the original character span.
4. Translate claims to English if needed; keep both versions and the translator's name and version.
5. A long thread produces several claims; each is verified separately and the post-level result lists them all.

Done when: a Hindi post and an English post each return atomic English claims with pointers to the original text, and a post
with only opinions returns no claims and says why.

### C6: Verdict rules (about 4 h)
Files: `claims/aggregate.py`, `claims/contradict.py`, `tests/unit/test_aggregate.py`.
Write the rule as a pure function so it can be tested without any model.

```
inputs: list[SupportResult], list[FactCheckMatch], list[Contradiction], config thresholds
1. supports    = results with support_prob >= tau_support and tier in {1, 2}
2. contradicts = fact-check matches rated false by a trusted checker (match similarity >= tau_match)
                 + official passages flagged by the contradiction check (score >= tau_contra)
3. context     = tier 1 or 2 passages that add a material qualifier to the supported core (found by the
                 qualifier check: a passage that the verifier scores as supporting a "however / only if / except"
                 restatement of the claim; thresholds in config)
4. if supports and contradicts:  verdict = UNVERIFIABLE_YET with flag CONFLICT, show both sides
5. elif supports and context:    verdict = MISSING_CONTEXT, show both passages
6. elif supports:                verdict = SUPPORTED
7. elif contradicts:             verdict = REFUTED
8. else:                         verdict = UNVERIFIABLE_YET   (includes the case where only tier 3 or 4 evidence exists,
                                 which is attached as context, never as support)
```

The tier list (which source id is Tier 1 to 4) lives in `config/sources.yaml`. Any change to it needs a line in
`docs/licences.md` or the claim sheet saying who approved it.

Contradiction sources:
- **Fact-check match.** Embed the claim, find reviewed claims above a similarity threshold, then confirm with the verifier that
  the reviewed claim's text supports our claim. Map the checker's rating text to true/false with a table in config
  (`rating_map.yaml`), and send unknown ratings to `UNVERIFIABLE_YET`, never guess.
- **Contradiction check.** Use a separate natural-language-inference model that outputs a contradiction score, applied to
  official passages only, and evaluate it separately before enabling it. If its error rate is too high on your data, leave it
  off and say so; fact-check matches alone still give `REFUTED`.

Tests: no evidence gives `UNVERIFIABLE_YET`; support alone gives `SUPPORTED`; support plus a qualifier gives `MISSING_CONTEXT`;
only Tier 3 or Tier 4 evidence gives `UNVERIFIABLE_YET`; conflict never averages; an unknown rating never maps to `REFUTED`;
thresholds and tiers change the result only through config.

Done when: the test file covers all branches and passes with no model installed.

### C7: Justification and the guard (about 4 h)
Files: `claims/justify.py`, `claims/guard.py`.
1. Give an LLM only the claim and the selected passages, labelled `[P1]`, `[P2]`. Ask for two or three sentences of explanation,
   each ending in the passage labels it relies on, and nothing else.
2. Split the output into sentences. For each, run the verifier with the **cited passage** as the document. Keep the sentence only
   if its support probability is above the guard threshold from config.
3. If no sentence survives, return no justification; the evidence cards still display. Never fall back to ungrounded text.
4. Store the guard's pass rate as a metric. A high drop rate means the model is drifting from the evidence.
5. HerO and HerO 2 fine-tune a model to write justifications and verdicts. You do not need that. The rule-based verdict plus a
   guarded justification is simpler and easier to explain. Keep fine-tuning as a stretch only.

Done when: a deliberately wrong sentence injected in a test is removed, and the guard pass rate appears in the metrics endpoint.

### C8: The agent and the gate (about 7 h, plus 2 h for the re-check scheduler in Tier 2)
Files: `agent/graph.py` (LangGraph state graph), `agent/tools.py`, `agent/trace.py` (server-sent events), `gate/policy.py`,
`config/policy.yaml`.
0. **Graph shape.** Nodes: `plan`, `tools` (parallel fan-out), `fuse`, `draft`, `verify`, `decide`. Edges: `decide` loops back
   to `plan` on a retry rule, or ends with a verdict or an abstain. The state holds the claim, the plan, the evidence so far and
   the budget used. Every node writes a trace event that the API streams to `GET /jobs/{id}/trace`. The same graph also hosts the
   provenance and forensics tools for media, so one trace shows the whole investigation. LangGraph is a library to learn on
   day 0; if its learning cost proves too high, the same shape can be a plain loop (see risks in the master map).
1. **Tools**, each a function with typed input and output: `search_sources(topic)`, `fetch(source_id, query)`,
   `retrieve(claim)`, `verify(claim, passages)`, `lookup_factcheck(claim)`, `translate(text, lang)`, `read_image_text(media)`
   (OCR or a vision model), and the media tools `reverse_search(media)` and `forensics(media)`, all behind the gate.
2. **Plan.** Choose sources from a topic-to-source table in config (scheme claims go to PIB and Data.gov.in, weather claims to
   IMD, and so on). The plan is a list of steps stored in the trace.
3. **Loop.** For each step: call the tool through the gate, record the result, then decide the next step by rule:
   - top support probability in the **grey band** → reformulate the query and try the next source;
   - a source raises `Unreachable` or `RateLimited` → record it and try an alternate allowed source;
   - supports and contradicts both found → fetch one more official source as a tie-breaker, then stop;
   - budget used up (steps, seconds, calls) → stop and return the best available verdict, often `UNVERIFIABLE_YET`.
4. **Gate** (a function every tool call passes through): domain allowlist from `policy.yaml`, per-claim limits, read-only tools only.
   Denials are returned to the agent as an error and logged. The planner may be an LLM, but the gate is plain code.
5. **Trace.** Write every step, tool input, output summary, error and decision to the ledger so the run can be replayed.
6. **Self-correction test.** Add a drill where the first source is made unreachable; the trace must show a different source used.
7. **Re-check scheduler (Tier 2).** Items labelled `UNVERIFIABLE_YET` get a `recheck_at` time from config. A scheduled job re-runs
   the agent for due items. A changed result writes a new ledger entry that points to the old one; it never edits it.

Done when: killing the first source's network access produces a trace with the error, the alternate source and a verdict, and
a request for a non-allowlisted domain appears under Denied actions.

### C9: Evaluation (about 6 h)
Files: `scripts/eval_claims.py`, `docs/claim-sheet.md`.
1. **Our set, built live:** reviewed claims from the Fact Check Tools API with ratings mapped through `rating_map.yaml`, with the
   leakage exclusion from C4. Keep Indian-language claims in their own slice.
2. Metrics: agreement with the human rating on answered claims, **coverage** (the share answered rather than `UNVERIFIABLE_YET`),
   and a coverage-versus-accuracy curve. Report errors by verdict type, language and source tier.
3. **Ablations**, each one line in the sheet: with and without query expansion; with and without the guard; each verifier model;
   with and without translation.
4. **AVeriTeC** (English): run a sample as a sanity check against a published task. Use the organisers' scorer if you can
   confirm it is the official one; otherwise write your own and say so. Do not compare directly with the shared-task leaderboard,
   because our system has a different setup.
5. **Error analysis:** read at least 30 wrong or abstained cases by hand; group them (retrieval miss, translation error,
   verifier error, rating mapping, claim not checkable) and put the table in the sheet.

Done when: the claim sheet's claims rows contain measured values and the error table exists.

### C10: Drills and UI (about 4 h)
- Claim in Hindi with no English coverage.
- Claim that matches no source.
- Two official sources that disagree.
- A source returning a changed page layout.
- A claim of several sentences.
- Very long pasted text.
- A claim that is really an opinion.
- Network lost during retrieval.
UI: show the atomic claims, the cited passage with its fetch age and source tier, the support probability as a band not a bare
number, the agent trace, denials, and a "translated" mark.

## 6. Pitfalls

- Reading "unsupported" as "false".
- Passing a multi-sentence claim to the verifier.
- Citing a model's paraphrase instead of the chunk the verifier scored.
- Leaving the 0.5 threshold uncalibrated, or calibrating and testing on the same data.
- Letting the reviewing fact-check article into retrieval during evaluation.
- Mapping unknown fact-check ratings to a verdict.
- Treating benchmark scores on model-written claims as accuracy on human claims.
- Using HerO's or Bespoke's code or weights without checking the licence, or installing a 70B model on a laptop.
- Swallowing source errors and returning an empty list.

## 7. Claim-sheet rows this track must fill

| Claim | Reproduce | Not claimed |
|---|---|---|
| Verifier balanced accuracy by model | `scripts/eval_verifier.py` | Accuracy on Indian civic claims |
| Calibrated threshold generalises to a held-out split | `scripts/calibrate_threshold.py` | Any other domain |
| Retrieval recall@k, with leakage excluded | `scripts/eval_retrieval.py` | Coverage of sources not indexed |
| Verdict agreement and coverage on live reviewed claims | `scripts/eval_claims.py` | Claims with no published review |
| Guard removes unsupported justification sentences | `pytest tests/unit/test_guard.py` | That surviving sentences are true |
| Agent switches source on failure | `pytest tests/drills/test_agent_failover.py` | Behaviour under sustained outage |
