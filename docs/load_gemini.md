# Gemini load test, 2026-10-31 21:02 local time

Command: `python scripts/load_gemini.py --users 1,3,6`. Live API, this key and network.

## What people saw

| Simultaneous users | Answered | Busy message | Other | Median s | Slowest s | Model calls |
|---|---|---|---|---|---|---|
| 1 | 0 | 0 | 1 | 4.6 | 4.6 | 1 (1.0 per message) |
| 3 | 0 | 0 | 3 | 19.9 | 24.0 | 3 (1.0 per message) |
| 6 | 0 | 0 | 6 | 28.0 | 32.0 | 4 (0.7 per message) |

## Model calls by purpose (all runs)

| Purpose (start of prompt) | ok | failed (class: count) |
|---|---|---|
| You read one social-media post and | 0 | QuotaExhausted: 8 |

## Not measured

- More than the listed number of simultaneous users, other days, a paid tier, or the Telegram path (it calls the same API).
- Whether a busy answer was a per-minute or a per-day limit.
