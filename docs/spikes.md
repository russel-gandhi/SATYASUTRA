# Day-0 spike results (ticket F0-08)

Run 2026-10-31 from the build machine (Windows 11, Python 3.14, RTX 4060 Laptop GPU). Results are what actually happened; nothing here is assumed to pass.

| Spike | Result | Evidence | Decision |
|---|---|---|---|
| Reverse image search | **Out of scope by team decision** | Google Cloud Vision is paid; removed from `config/sources.yaml` | Fallback adopted: own pHash/PDQ image index, plus operator-assisted Lens/Yandex lookups for demo items. Provenance labels fall back to `PROVENANCE_LIMITED` when the index has no match |
| TruFor on this machine | **Not run (conditional)** | Repo `grip-unina/TruFor` is live and not archived (last push 2025-05-29). GitHub reports no standard licence; terms are in its own `LICENSE.txt`, which the team has not read yet. An NVIDIA RTX 4060 is present. Weights were not downloaded | Open: read `LICENSE.txt` (is use in a hackathon allowed?), then download weights with the team's approval and time one image. Fallback per plan: keyframes only or smaller input |
| LLM provider | **Pass** | Gemini key lists 46 models, but the list includes ones closed to new keys (`gemini-2.5-flash` and `-flash-lite` return 404 "no longer available to new users"). `gemini-3.5-flash-lite` returned schema-valid JSON in 1.7 s (95 tokens). `gemini-3.8-flash` timed out at 150 s twice | Use `gemini-3.5-flash-lite` as the working default. Re-test `3.8-flash` before relying on it. Free-tier quota and rate limits are **not yet measured** |
| Telegram bot | **Partial** | `getMe` returned 200: the token is valid | Echo reply not tested (needs a running bot, a Phase 1 task). Token was pasted in chat on 2026-10-31, so it should be revoked and re-issued before any public demo |
| Fact Check Tools API key | **Pass** | A search for "vaccine" returned 3 real claims with publisher, rating and review date | Keep as Tier 2 source |
| LangGraph | **Pass** | `langgraph` 1.2.18 installs on Python 3.14; a conditional loop graph ran to the expected result. Streaming of events was not tested | Use LangGraph; test streaming in Phase 1 |
| LangGraph streaming to SSE (F1-03) | **Pass** | On Python 3.14 with `langgraph` 1.2.18, `graph.astream(stream_mode=["tasks"])` yields a start event and a finish event per node as each node runs. `GET /trace/stream?job=` sends them as Server-Sent Events over a real local HTTP connection (uvicorn). Three-node graph with 0.2 s per node: first event arrived in a median 16 ms (min 3, max 31, 15 runs) while the run took about 620 ms; the first event arrives before the last node finishes (test). A client that leaves mid-run closes the stream, the open-stream counter returns to zero, later nodes never run and no task is left behind (test). The 7 tests passed 3 runs in a row. | Use LangGraph; no fallback loop needed. Findings: the task `error` field carries the exception message, so events report only the exception class; LangGraph silently drops update keys the state schema lacks, so the real agent state must declare `tool_calls`. Not tested: many simultaneous clients, behind a proxy, or a reconnect with `Last-Event-ID` (the id is sent, resuming is not built); the real agent graph (Phase 3) |
| Organiser question Q2 (LLM provider rule) | **Answered by the team** | Team decision: Gemini | Recorded |
| Organiser question Q15 (reusing ideas from the SUTRA plan) | **Not confirmed by the organisers** | Team states the Satyapan and SUTRA plans are both its own work, with no code written before the event | Proceeding on the team's own statement. **Ask the organisers in writing and record the reply here** |

## Still open

1. Read TruFor `LICENSE.txt`, then decide whether to download weights.
2. Written reply to Q15 from the organisers.
3. Measure Gemini free-tier rate limits and daily quota.
4. Telegram echo test, and revoke/re-issue the exposed token.
5. Retest `gemini-3.8-flash`; the 150 s timeout may be transient.
