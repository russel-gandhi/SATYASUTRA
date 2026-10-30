"""Hits the real Bluesky Jetstream. Run with `pytest -m contract`."""

import asyncio

import pytest

from satyasutra.config import load_config
from satyasutra.ingest import IngestStats, stream_posts

pytestmark = pytest.mark.contract
WAIT_S = 60


def test_a_live_post_arrives_and_validates():
    cfg = load_config().runtime.ingest

    async def first():
        stats = IngestStats()
        async for post in stream_posts(cfg, stats, "contract-test-salt"):
            return post, stats

    post, stats = asyncio.run(asyncio.wait_for(first(), WAIT_S))
    assert post.id.startswith("at://") and post.text and post.source_id == cfg.source_id
    assert stats.malformed == 0
