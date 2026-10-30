"""Real fact-checker feeds and real images. Run with `pytest -m contract`."""

import sqlite3
from pathlib import Path

import httpx
import pytest

from satyasutra.config import load_config
from satyasutra.envfile import load_env_file
from satyasutra.provenance import ImageIndex, download_image, fingerprint
from satyasutra.provenance.build import build_index
from satyasutra.sources.probe import build_user_agent

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]


def test_the_live_feeds_fill_an_index_and_an_indexed_image_matches_itself_after_a_fresh_download(tmp_path):
    load_env_file(ROOT / ".env")
    cfg = load_config()
    index = ImageIndex(tmp_path / "p.sqlite3")
    stats = build_index(cfg, index)
    assert index.count() > 0, [(f.source_id, f.error) for f in stats.feeds]
    assert any(not f.error for f in stats.feeds)

    with sqlite3.connect(tmp_path / "p.sqlite3") as con:
        image_url, article_url = con.execute("SELECT image_url, article_url FROM items LIMIT 1").fetchone()
    with httpx.Client(timeout=cfg.runtime.provenance.download_timeout_s, headers={"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}, follow_redirects=True) as http:
        data = download_image(http, image_url, cfg.runtime.provenance.max_image_bytes, "contract")
    assert data is not None
    cut = cfg.thresholds.thresholds["image_match_pdq_distance"].value
    hits = index.nearest(fingerprint(data), cut, cfg.runtime.provenance.match_limit)
    assert hits and hits[0].distance == 0 and article_url in {h.source.article_url for h in hits}
