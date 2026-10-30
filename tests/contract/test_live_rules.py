"""Live Fact Check data through the verdict rules. Run with `pytest -m contract`.

Live results change, so these check what must hold whatever the API returns, not a particular label."""

from pathlib import Path

import pytest

from satyasutra.cache import TTLCache
from satyasutra.claims.rules import decide
from satyasutra.config import load_config
from satyasutra.envfile import load_env_file
from satyasutra.schemas import Claim
from satyasutra.sources import FactCheckClient

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]
CLAIMS = ["The earth is flat", "Smoking causes lung cancer", "Eating garlic prevents coronavirus infection", "Vaccines cause autism in children"]


def test_every_decisive_live_verdict_rests_on_same_claim_reviews_with_a_mapped_rating(tmp_path):
    load_env_file(ROOT / ".env")
    cfg = load_config()
    cut = cfg.thresholds.thresholds["factcheck_relevance"].value
    client = FactCheckClient(cfg, TTLCache(tmp_path / "c.sqlite3"))
    for text in CLAIMS:
        found = client.search(text)
        assert found.evidence, f"the live API returned nothing for {text!r}"
        out = decide(Claim(id="c", post_id="p", text_en=text, text_orig=text, span=(0, len(text)), lang="en"), found.evidence, cfg)
        v = out.verdict
        assert "COERCED" not in v.flags
        if v.label in ("REFUTED", "SUPPORTED", "MISSING_CONTEXT"):
            from satyasutra.claims.rules import relevance
            assert all(relevance(text, e.reviewed_claim) >= cut for e in v.evidence), text
        # a true claim must never be refuted by a review of its opposite, which the live API returns for it
        if text == "Smoking causes lung cancer":
            assert v.label == "UNVERIFIABLE_YET" and out.related
