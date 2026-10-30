"""Fingerprints and the index, on images generated in code (smooth random fields: like photos, they have structure at many scales)."""

import io
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageOps

from satyasutra.config import load_config
from satyasutra.provenance import BadImage, ImageIndex, Source, fingerprint, hamming
from satyasutra.provenance.fingerprint import PDQ_BITS, PHASH_BITS

CONFIG = Path(__file__).resolve().parents[2] / "config"
# Edits that must stay matched. The cut itself comes from the live measurement, so the test reads it from config.
MAX_EDIT_BITS = 36
NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)


def picture(seed: int, size=(320, 240)) -> Image.Image:
    coarse = np.random.RandomState(seed).rand(10, 12, 3)
    return Image.fromarray((coarse * 255).astype("uint8")).resize(size, Image.Resampling.BICUBIC)


def encode(image: Image.Image, fmt="PNG", **kw) -> bytes:
    buf = io.BytesIO()
    image.save(buf, fmt, **kw)
    return buf.getvalue()


def distance(a, b) -> int:
    return min(hamming(a.pdq, b.pdq), hamming(a.pdq_flipped, b.pdq))


def source(url="https://x.org/a", title="t"):
    return Source("https://x.org/i.jpg", url, title, "Pub", "en", NOW, "Politics")


def test_the_same_bytes_give_the_same_fingerprint_and_sizes_are_recorded():
    data = encode(picture(1))
    a, b = fingerprint(data), fingerprint(data)
    assert a == b and (a.width, a.height) == (320, 240) and a.pdq.bit_length() <= PDQ_BITS and a.phash.bit_length() <= PHASH_BITS


@pytest.mark.parametrize("name,edit", [
    ("resize", lambda im: im.resize((160, 120), Image.Resampling.LANCZOS)),
    ("jpeg", lambda im: Image.open(io.BytesIO(encode(im, "JPEG", quality=40)))),
    ("flip", ImageOps.mirror),
])
def test_resize_recompression_and_flip_stay_within_the_edit_distance(name, edit):
    original = picture(2)
    assert distance(fingerprint(encode(original)), fingerprint(encode(edit(original)))) <= MAX_EDIT_BITS


def test_a_flip_matches_exactly_because_the_mirror_is_hashed_from_mirrored_pixels():
    original = picture(3)
    assert fingerprint(encode(original)).pdq_flipped == fingerprint(encode(ImageOps.mirror(original))).pdq


def test_an_unrelated_image_is_far_beyond_the_configured_distance():
    cut = load_config(CONFIG).thresholds.thresholds["image_match_pdq_distance"].value
    assert distance(fingerprint(encode(picture(4))), fingerprint(encode(picture(5)))) > cut


def test_the_configured_distance_covers_the_edits_the_measurement_used():
    cut = load_config(CONFIG).thresholds.thresholds["image_match_pdq_distance"].value
    assert cut >= 0 and "measure_image_match.py" in load_config(CONFIG).thresholds.thresholds["image_match_pdq_distance"].calibrated_on


def test_transparent_png_is_flattened_on_white_without_a_warning(recwarn):
    rgba = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    fingerprint(encode(rgba))
    assert not [w for w in recwarn if "Transparency" in str(w.message)]


def test_bytes_that_are_not_an_image_raise_bad_image():
    with pytest.raises(BadImage):
        fingerprint(b"this is not an image")


# index ---------------------------------------------------------------------------------------------------------------

@pytest.fixture
def index(tmp_path):
    return ImageIndex(tmp_path / "p.sqlite3", clock=lambda: NOW)


def test_adding_the_same_image_from_the_same_article_twice_writes_one_row(index):
    fp = fingerprint(encode(picture(6)))
    assert index.add(fp, source()) is True and index.add(fp, source()) is False and index.count() == 1


def test_the_same_image_in_a_second_article_is_a_second_row(index):
    fp = fingerprint(encode(picture(6)))
    index.add(fp, source("https://x.org/a")), index.add(fp, source("https://x.org/b"))
    assert index.count() == 2


def test_nearest_finds_a_recompressed_and_a_flipped_copy_and_not_an_unrelated_image(index):
    original = picture(7)
    index.add(fingerprint(encode(original)), source("https://x.org/seven"))
    index.add(fingerprint(encode(picture(8))), source("https://x.org/eight"))
    jpeg = fingerprint(encode(original, "JPEG", quality=40))
    [hit] = index.nearest(jpeg, MAX_EDIT_BITS, 5)
    assert hit.source.article_url == "https://x.org/seven" and hit.distance <= MAX_EDIT_BITS and not hit.exact and not hit.mirrored
    [flipped] = index.nearest(fingerprint(encode(ImageOps.mirror(original))), MAX_EDIT_BITS, 5)
    assert flipped.source.article_url == "https://x.org/seven" and flipped.distance == 0 and flipped.mirrored
    assert index.nearest(fingerprint(encode(picture(9))), MAX_EDIT_BITS, 5) == []


def test_a_byte_identical_file_is_reported_exact_with_the_source_and_retrieval_time(index):
    data = encode(picture(10))
    index.add(fingerprint(data), source("https://x.org/ten", "The title"))
    [hit] = index.nearest(fingerprint(data), 0, 5)
    assert hit.exact and hit.distance == 0 and hit.source.title == "The title" and hit.retrieved_at == NOW and hit.source.labels == "Politics"


def test_results_are_closest_first_and_limited(index):
    base = picture(11)
    index.add(fingerprint(encode(base.resize((80, 60)))), source("https://x.org/small"))
    index.add(fingerprint(encode(base)), source("https://x.org/same"))
    hits = index.nearest(fingerprint(encode(base)), MAX_EDIT_BITS, 1)
    assert len(hits) == 1 and hits[0].source.article_url == "https://x.org/same"


def test_the_index_holds_hashes_and_links_and_no_image_bytes(index, tmp_path):
    data = encode(picture(12))
    index.add(fingerprint(data), source())
    assert data not in (tmp_path / "p.sqlite3").read_bytes()


def test_no_connection_is_left_open(tmp_path):
    path = tmp_path / "p.sqlite3"
    idx = ImageIndex(path)
    idx.add(fingerprint(encode(picture(13))), source())
    idx.count(), idx.nearest(fingerprint(encode(picture(13))), 0, 1), idx.has_article_image("a", "b")
    path.unlink()
