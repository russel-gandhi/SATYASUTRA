"""Hits the real Google Fact Check Tools API with the team's key. Run with `pytest -m contract`."""

from pathlib import Path

import pytest

from satyasutra.cache import TTLCache
from satyasutra.config import load_config
from satyasutra.envfile import load_env_file
from satyasutra.schemas.core import sha256_text
from satyasutra.sources import FactCheckClient

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]


def test_a_real_topic_returns_valid_evidence_and_the_second_call_is_cached(tmp_path):
    load_env_file(ROOT / ".env")
    client = FactCheckClient(load_config(), TTLCache(tmp_path / "c.sqlite3"))
    first = client.search("vaccine causes autism", language="en")
    assert first.evidence, "the live API returned no reviews for a well-covered topic"
    for ev in first.evidence:
        assert ev.rating and ev.url.startswith("http") and ev.passage_sha256 == sha256_text(ev.passage) and ev.tier == 2
    second = client.search("Vaccine  causes autism", language="en")
    assert second.from_cache and [e.url for e in second.evidence] == [e.url for e in first.evidence]
