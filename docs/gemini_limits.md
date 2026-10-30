# Gemini limits and latency (ticket F1-02)

Measured 2026-10-30 20:04 UTC against the live API with the team's key. Calls used: 28 of the 40 cap.
Per-day quotas cannot be measured without exhausting them; where the API states a quota in a 429 body it is quoted below, otherwise it is **not measured**.

## Latency, short structured-output call

| Model | Samples | OK | Median s | p95 s | Tokens per call | Statuses |
|---|---|---|---|---|---|---|
| gemini-3.5-flash-lite | 8 | 8 | 1.19 | 1.62 | 46 | [200] |

## Burst on gemini-3.5-flash-lite

- 17 calls in 3.9 s, 14 succeeded (about 218 per minute at this speed).
- First 429 reached. Message: 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.'; retry delay: 2s.
  - quota `GenerateRequestsPerMinutePerProjectPerModel-FreeTier` value 15

## Re-test of slower models

| Model | Attempts | OK | Timeouts | Seconds per OK |
|---|---|---|---|---|
| gemini-3.8-flash | 3 | 0 | 2 | - |
