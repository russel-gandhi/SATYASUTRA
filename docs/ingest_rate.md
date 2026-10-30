# Live ingest rate (ticket F1-04)

Measured 2026-10-30 20:11 UTC for 60 s from the live Bluesky Jetstream, filters from `config/runtime.yaml` (languages ['en', 'hi'], minimum 40 characters, replies skipped).

| Measure | Value |
|---|---|
| Events received | 2995 (49.7 per second) |
| Posts passing the filters | 668 (11.08 per second, 665 per minute) |
| Skipped, by reason | language: 432, not_commit: 31, not_create: 113, reply: 1365, too_short: 386 |
| Malformed | 0 |
| Reconnects | 0 |

One run on one network, at one time of day: treat it as an order of magnitude, not a constant.
