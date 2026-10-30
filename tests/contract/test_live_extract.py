"""Hits the real Bluesky Jetstream and the real Gemini API. Run with `pytest -m contract`."""

import asyncio
from pathlib import Path

import pytest

from satyasutra.claims import extract_claims
from satyasutra.config import load_config
from satyasutra.envfile import load_env_file
from satyasutra.ingest import IngestStats, stream_posts
from satyasutra.llm import GeminiClient

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]
WAIT_S = 90


def test_a_real_post_goes_through_the_live_model_and_every_output_validates():
    load_env_file(ROOT / ".env")
    cfg = load_config()

    async def first_post():
        async for post in stream_posts(cfg.runtime.ingest, IngestStats(), "contract-test-salt"):
            return post

    post = asyncio.run(asyncio.wait_for(first_post(), WAIT_S))
    result = extract_claims(GeminiClient(cfg), cfg, post)
    assert result.status in ("ok", "no_items", "invalid_output"), result.reason       # a live-model refusal or outage fails the test
    for claim in result.claims:
        assert post.text[claim.span[0]:claim.span[1]] == claim.text_orig and claim.text_en
    assert result.prompt_sha256 and result.model
