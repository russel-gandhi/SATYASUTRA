# Pulse throughput, measured 2026-10-31 14:28 UTC

Command: `python scripts/bench_pulse.py --runs 3`. Live feeds, this machine, one network. Numbers change with the news.

## Refresh (all feeds, parallel)

- 3 runs, seconds: 6.0, 4.5, 4.0 (median 4.5)
- Workers: 8; cache: 300 s, so the page answers from memory between refreshes.

## Funnel (last run)

| Stage | Count |
|---|---|
| Feeds configured (plus Google Trends) | 22 |
| Feeds that answered | 22 |
| Items read from feeds (one pass) | 746 |
| Headlines kept (newer than 36.0 h) | 454 |
| Fact-checker articles shown | 20 |
| Trending terms | 10 |
| Story clusters | 17 |
| Clusters carried by 2+ outlets | 17 |

## Per feed (one fetch each, sequential)

| Feed | Result | Items | Seconds |
|---|---|---|---|
| gnews_en | ok | 38 | 0.59 |
| gnews_nation_en | ok | 64 | 0.61 |
| gnews_hi | ok | 26 | 0.10 |
| gnews_kn | ok | 26 | 0.24 |
| livehindustan_hi | ok | 21 | 0.14 |
| hindu_in | ok | 60 | 0.13 |
| toi_top | ok | 47 | 0.13 |
| ndtv_top | ok | 20 | 1.97 |
| ht_india | ok | 100 | 0.15 |
| ie_india | ok | 200 | 0.31 |
| mint | ok | 35 | 0.19 |
| indiatoday_nation | ok | 20 | 0.12 |
| boom_en | ok | 2 | 0.58 |
| boom_hi | ok | 7 | 0.63 |
| altnews_en | ok | 10 | 0.82 |
| altnews_hi | ok | 10 | 0.41 |
| factly_en | ok | 10 | 0.14 |
| vishvas_en | ok | 10 | 0.15 |
| vishvas_te | ok | 10 | 0.07 |
| newschecker_en | ok | 10 | 0.66 |
| newschecker_hi | ok | 10 | 0.60 |
| newschecker_kn | ok | 10 | 0.58 |

## Matching a pasted claim against the snapshot

- 20 probes (latest fact-checker headlines used as claims): median 9.7 ms, slowest 23.8 ms.

## Not measured

- Behaviour under many simultaneous visitors (the cache means one refresh serves all, but this was not load-tested).
- The Bluesky stream worker (off by default; see docs/ingest_rate.md for its earlier funnel).
- Feeds from other networks or at other times of day.
