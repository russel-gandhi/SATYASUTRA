# Gemini load test, 2026-11-01 01:29 local time

Command: `python scripts/load_gemini.py --users 1,3,6`. Live API, this key and network.

## What people saw

| Simultaneous users | Answered | Busy message | Other | Median s | Slowest s | Model calls |
|---|---|---|---|---|---|---|
| 1 | 1 | 0 | 0 | 13.7 | 13.7 | 4 (4.0 per message) |
| 3 | 3 | 0 | 0 | 37.2 | 48.0 | 12 (4.0 per message) |
| 6 | 4 | 0 | 2 | 52.6 | 64.6 | 16 (2.7 per message) |

## Model calls by purpose (all runs)

| Purpose (start of prompt) | ok | failed (class: count) |
|---|---|---|
| You compare one numeric detail in | 8 | none |
| You list the named entities in | 8 | none |
| You read news headlines and say | 8 | none |
| You read one social-media post and | 8 | none |

## Not measured

- More than the listed number of simultaneous users, other days, a paid tier, or the Telegram path (it calls the same API).
- Whether a busy answer was a per-minute or a per-day limit.

## What this run found (2026-11-01, replaces the exhausted-quota finding of 2026-10-31)

- **A claim check costs 4 model calls** (read the post, name entities, label headlines, compare numbers). With the free tier's 15 calls per minute per model, that is at most about 3 claim checks a minute for everyone together.
- **It slows down in a queue rather than failing, up to a point:** 1 user waited 13.7 s, 3 simultaneous users a median of 37 s (slowest 48 s), 6 simultaneous users a median of 53 s (slowest 65 s). At 6, 4 of 6 were answered and 2 ended as "other"
  (a stream error event, not a verdict; the script does not record which limit it hit). So the live demo should not have more than about 3 people checking claims at the same moment on the free tier.
- Calls per message fell to 2.7 at 6 users, which fits the 2 that stopped early, not any saving.
- A paid tier or a second key would change these numbers; neither was tested.
