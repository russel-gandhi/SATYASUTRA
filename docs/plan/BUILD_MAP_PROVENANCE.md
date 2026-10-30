# Provenance build map: where did this come from, and how did it change

Instructions for whoever writes the `provenance` and `graph` modules. This track is new in the merged plan. It comes from the
SUTRA plan's idea that provenance, not just a verdict, is the main answer to the most common kind of civic misinformation:
**real media reused out of context**. The claim that this is the most common kind comes from that plan; we have not measured it.

Each phase lists files, steps and a **done when** check. Hours are guesses.

**Revision 2.** Two additions feed this track: video keyframes now also go to TruFor (forensics FO-26), so a keyframe can carry
both an earlier appearance and an edit map; and the variant graph stores **entities linked through Wikidata** (claims C11), so
the provenance view can show every cluster that mentions the same scheme, person or place. When the out-of-context rule fires,
its evidence feeds the bot's **correction card** ("This photo is from 2019, not from yesterday", with the earlier link; Bible
D47).

## 1. What this track decides

| Question | Answer | Output |
|---|---|---|
| Have we seen this file or a near copy before? | Fingerprint lookup in our own store | Cluster id, internal first-seen time |
| Is there an older copy somewhere on the web? | Reverse image search, link archive lookup | `earlier_appearance` evidence with URL, date, snippet |
| Does the file carry a signed origin? | C2PA manifest read | Verified, broken or absent |
| Does the earlier copy contradict the claimed date or place? | Comparison of dates and places | `OUT_OF_CONTEXT` or neutral |
| How did the claim's wording change as it spread? | Variant graph with word-level diff | Patient zero, mutation path, amplifiers |

**Provenance outranks classifier scores.** An earlier appearance with a different date or place is hard evidence. A detector
score is soft evidence. The UI shows them in that order.

**No hit is not authentic.** Finding no earlier appearance means only that we found none. It never produces
`AUTHENTIC_IN_CONTEXT` on its own. That label needs a valid credential, or a consistent earliest-copy match plus detectors that
reliably find nothing.

## 2. Tools and how each is used

I did not verify any of these third-party facts. They come from the SUTRA plan and general knowledge; check each page before
relying on it.

| Tool | Role | Cautions |
|---|---|---|
| Google Cloud Vision, web detection | Returns matching images, pages that contain them, and labels. The main reverse-search source | Paid beyond a small free allowance; check current pricing and terms. Needs a key |
| Our own fingerprint index | pHash and PDQ for images, sampled keyframes for video | PDQ and its video companion come from Meta's open-source ThreatExchange project; check that repository's licence |
| Chromaprint | Audio fingerprints (optional) | Check its licence |
| C2PA reader (`c2pa-python`) | Reads signed content credentials | Absence means nothing |
| `exiftool` | EXIF and XMP metadata | Messaging apps strip metadata; absence is neutral |
| `ffmpeg` scene filter | Pull keyframes from video for reverse search | Call with an argument list, never a shell string |
| Internet Archive CDX API | When a link first existed | Rate limits; check its usage policy |
| Postgres tables and recursive queries | The variant graph | Neo4j is an optional mirror (Tier 3) |

## 3. Decisions

1. **Postgres first, Neo4j later.** The queries needed (earliest item, variants in time order, top accounts) are simple. A graph
   database is an optional mirror (PV-5).
2. **Budgeted reverse search.** Every call goes through the gate, which enforces a daily limit from config. Results are cached by
   image hash. When the budget is gone or no key is set, provenance falls back to our own index and the verdict carries the flag
   `PROVENANCE_LIMITED`.
3. **Hash both orientations.** Store the fingerprint of the image and of its horizontally flipped copy, since flipping is a common
   evasion.
4. **Earliest means earliest observed.** We say "earliest item we saw", never "origin".
5. **A page date must come from the page.** Use publication metadata, structured data or an archive timestamp. Never infer a
   date from our fetch time. A page with no trustworthy date gives no date evidence.
6. **Hash account identifiers.** The public graph shows hashed handles, with names only for verified public figures.
7. **No video downloading in Tier 1.** Work from files users send and from images and keyframes. Fetching video from platforms
   raises terms-of-service questions.

## 4. Phases

### PV-1: Provenance core (lane 3, about 5 h)
Files: `provenance/intake.py`, `provenance/fingerprint.py`, `provenance/credentials.py`, `tests/unit/test_provenance_core.py`.
1. **Validate first.** Detect the real file type from its content, not its extension, using `ffprobe` or a content-sniffing
   library. Reject unsupported or corrupt files with a reason. Accepted formats come from config.
2. **Hash.** Compute SHA-256 of the bytes. For images compute pHash and PDQ, and the same for the horizontally flipped image.
   For video, extract keyframes with the ffmpeg scene filter (threshold in config), cap their number, and fingerprint each.
3. **Internal first-seen.** Look up fingerprints within a distance threshold (config) in the `media` table. Return the cluster
   and the earliest `first_seen_ts`. Insert the new row with its own timestamp.
4. **Credentials.** Read the C2PA manifest where present and verify its signature chain; report `verified`, `broken` or `absent`.
   A manifest saying the file was AI-generated is strong evidence for `SYNTHETIC`.
5. **Metadata.** Read EXIF and XMP with `exiftool`, called with an argument list. Keep camera, date and place only if present.
   Stripped metadata is recorded as "stripped" and treated as neutral.
6. Return a `ProvenanceReport` with every field, plus reasons for any step that could not run.

Done when: a tiny image, a corrupt file, a re-encoded copy, a flipped copy and a file with credentials each return the right
fields or a clean reason.

### PV-2: Reverse search and the out-of-context rule (lane 3, about 5 h)
Files: `provenance/reverse_search.py`, `provenance/page_dates.py`, `provenance/ooc_rule.py`, `tests/unit/test_ooc_rule.py`.
1. **Adapter.** Call the reverse-search service for the image (for video, for each keyframe). Request the matching images and
   the pages that contain them. Cache by image hash. Return typed errors (`Unreachable`, `RateLimited`, `QuotaExhausted`).
2. **Budget guard** in the gate: a daily call limit from config; on exhaustion set `PROVENANCE_LIMITED` and continue with
   internal provenance only.
3. **Page dates.** For each matching page, fetch it through the gate (allowlist, read-only, size limit) and extract a
   publication date from page metadata and structured data; fall back to an archive timestamp. Pages with no date are kept as
   context but give no date evidence.
4. **Rule.** Using the claim's `time_ref` and `place_ref` from the claims track:
   ```
   earliest = the earliest trustworthy page date among matches
   if earliest exists and earliest < claimed_event_time - tolerance:    -> OUT_OF_CONTEXT (date)
   if a matching page's text or caption clearly names a different place
      than place_ref (verified by the verifier against the cited text):  -> OUT_OF_CONTEXT (place)
   else:                                                                 -> no provenance verdict (neutral)
   ```
   Tolerance and the strictness of the place check come from config. The evidence record holds the URL, the date and the
   snippet that shows it.
5. **Quality of evidence.** Prefer matches whose pages are in higher source tiers; a social repost is context, not proof.
6. **False accusations.** Add a test with a brand-new original photo (a consenting teammate's) that must not be marked
   `OUT_OF_CONTEXT`.

Done when: a known recirculated image returns an earlier appearance with a date and URL, and a fresh original photo returns no
verdict.

### PV-3: Link archive lookup (Tier 2, about 2 h)
For a link in a post, query the archive's CDX service for the oldest successful capture and report that date as "first
existed". Handle no capture, rate limits and redirects. Done when a known old URL returns its earliest capture.

### PV-4: Variant graph and mutation diff (lane 1, about 5 h)
Files: `graph/schema.sql`, `graph/queries.py`, `graph/diff.py`, `api/routes/provenance.py`.
1. **Tables:** `claims`, `variants` (text, language, MinHash, first_seen), `posts` (platform, url, time, engagement, variant id,
   account hash), `accounts` (hash, platform, created time if exposed), `media`, `media_edges` (near-duplicate distance),
   `earlier_appearances` (media id, URL, date, source), `evidence`, and from claims C11 `entities` (Wikidata id, label, type)
   and `claim_entities`.
2. **Patient zero:** the earliest of all post times and earlier-appearance dates in the cluster. Label it "earliest item we saw".
3. **Mutation path:** variants ordered by first-seen time. Between consecutive variants compute a word-level diff with Python's
   `difflib`, and highlight changed numbers and named places separately, since "50" becoming "500" is the case that matters.
4. **Amplifiers:** group posts by account hash within the cluster, rank by post count, and show account age where the platform
   exposes it. Present results as "coordinated-looking activity", never as "bots".
5. **Reshare chains** (where the feed exposes reposts): use a recursive query.
6. Endpoint `GET /clusters/{id}/provenance` returns the earliest item, variants with diffs, amplifiers, earlier appearances and
   linked entities, with a link to other clusters that share an entity.

Done when: a live cluster with at least three variants shows its earliest item and a word-level diff between variants.

### PV-5: Neo4j mirror (Tier 3, about 6 h)
Only if there is time and a demo reason. Mirror the same data into a graph database and confirm that the same three queries
return the same answers. Keep Postgres as the source of truth.

## 5. Drills

No earlier appearance; reverse-search quota exhausted; no key configured; a matching page with no date; a matching page with a
conflicting date; a flipped image; a screenshot of a screenshot; a cropped image; a video with no keyframes; a very large image;
two clusters with the same image and different captions (a context-recycling signal); the archive service down.

## 6. Evaluation

1. **Recall of earlier appearance:** from live fact-checks about recirculated images, find the origin the fact-checker states,
   and measure how often the pipeline finds an earlier appearance. Exclude the fact-check page and its domain from the results.
2. **False `OUT_OF_CONTEXT` rate** on fresh original photos from consenting teammates and on clearly new items.
3. **Fingerprint robustness:** re-encodes, crops, flips and screenshots from a degradation ladder; report the match rate by
   condition.
4. **Date extraction accuracy** on a hand-checked sample of pages.
5. Report each with its command in the claim sheet, and state what is not claimed (for example, recall on images that exist only
   in closed groups).

## 7. Pitfalls

- Treating "no hit" as "authentic", or "earliest we saw" as "origin".
- Using fetch time as page date.
- Letting social reposts count as earliest evidence without a trustworthy date.
- Ignoring the reverse-search budget until the demo.
- Passing filenames to a shell.
- Letting the evaluated fact-check page into the results.
- Exposing raw account handles in the public graph.
- Downloading platform video in Tier 1.
