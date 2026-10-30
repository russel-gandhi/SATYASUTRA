# Debunked-image index

What it is: a SQLite table of perceptual hashes of images that appeared in articles by fact-checkers, with the link to the
article, the publisher, the date and the feed's category tags. It replaces paid reverse image search (decision in `docs/spikes.md`).
Build it with `python scripts/build_index.py`, query it with `python scripts/match_image.py <file-or-url>`.

## What a match means

**A match means: a picture very close to this one appeared in an article by that fact-checker.** Nothing more.

- It does **not** say the claim attached to the picture is false. Fact-checkers also publish pictures that turned out to be genuine,
  and an article may show many pictures (screenshots of the viral post, the original, a map).
- It does **not** say the picture was used in the same context. Context is a separate question for the verdict rules.
- It is a lead for provenance. A verdict needs evidence that passes the source-tier rule (F1-07); this index never produces one.
- **No match says nothing.** The index holds only what the feeds have published recently. An image can be old, fake, recycled and
  still absent.

## What is stored

Hashes (SHA-256 of the file, 64-bit pHash, 256-bit PDQ with its quality score), the image URL, the article URL, title, publisher,
language, the article's publication date, the feed's category tags and when we retrieved it. **Images are downloaded into
memory, hashed and discarded**; a test checks that the database does not contain the image bytes.

The "labels" column holds the RSS category tags of the article (for example "Fact Check", "Politics"). **RSS feeds carry no structured
rating**, so this is not the fact-checker's verdict. The Google Fact Check API remains the source of rating text (F1-06).

## How matching works

PDQ Hamming distance (0 to 256 bits) between the query and each stored hash, taking the smaller of the query as given and the
query mirrored, so a horizontally flipped repost matches. The mirror is hashed from the mirrored pixels. A scan of every row; the
index has hundreds of rows, not millions.

The distance cut is `image_match_pdq_distance` in `config/thresholds.yaml`, chosen from `scripts/measure_image_match.py`
(see "Measured" below).

## What it cannot do

- **Crops.** A 90% centre crop moves most images far past any safe distance (see the table below). Cropped reposts are missed.
- **Overlays and edits**: added text, emoji, borders, collages and screenshots of a post that contains the image change the hash.
- **Site furniture.** Feeds contain every image in an article, including the site's own badge or logo. One FactCheck.org badge
  appeared in 6 articles. A query image that happens to contain such a badge could match it. The index does not yet filter
  boilerplate; matches should be read with the article title, which `match_image.py` prints.
- **Coverage.** Five feeds, ten or fewer recent articles each. Full Fact and Science Feedback were reachable but their feeds
  contain no images, so they are not used. Older debunks are only found if the feed still lists them.
- **Flat or tiny images.** PDQ reports a quality score; low quality hashes are unreliable. Images under `min_side_px` are not indexed.
- **Terms of use.** Only public RSS feeds and the images they link are fetched, at the configured rate with a descriptive
  User-Agent. Each site's terms and robots rules were **not** read for this ticket; read them before running the index at scale.

## Measured

Command: `python scripts/measure_image_match.py measure` (2026-10-31). It draws random Wikimedia Commons images (thumbnails, held in
memory), hashes them, and counts how many of the unrelated pairs fall within each distance, and how many edited copies still match.

Sample: 60 distinct images, 1,770 unrelated pairs. Share of edited copies still matched:

| PDQ distance | false matches | resize 50% | JPEG q40 | flip | 90% centre crop |
|---|---|---|---|---|---|
| 8 | 0 | 75% | 100% | 100% | 0% |
| 16 | 0 | 98% | 100% | 100% | 0% |
| 32 | 0 | 98% | 100% | 100% | 0% |
| **40 (chosen)** | **0** | **100%** | **100%** | **100%** | **0%** |
| 64 | 0 | 100% | 100% | 100% | 12% |
| 96 | 0 | 100% | 100% | 100% | 77% |
| 100 | 2 (0.11%) | 100% | 100% | 100% | 80% |
| 104 | 11 (0.62%) | 100% | 100% | 100% | 85% |

Largest edit distance for resize, JPEG and flip: 36. Closest unrelated pair: 100. The cut of 40 sits above every non-crop edit
and 60 bits below the nearest unrelated pair. Going higher to catch crops (needs about 96 or more) would sit next to the point where
false matches begin, so crops are deliberately not supported.

Limits of this evidence: one sample of 60 random Commons photos. Debunked material is often screenshots, memes and flat graphics, which
hash less distinctively than photos; false matches among those are **not** bounded by this table. Re-run the measurement on a
sample of the images in the index before relying on the cut for them.
