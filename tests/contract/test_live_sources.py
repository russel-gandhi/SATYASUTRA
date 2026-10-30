"""Hits the real internet. Run with `pytest -m contract` before any demo (plan: contract tests stay honest)."""

import pytest

from satyasutra.config import load_config
from satyasutra.sources import probe_all

pytestmark = pytest.mark.contract


def test_every_configured_source_answers_with_a_known_state():
    results = probe_all(load_config())
    assert results, "no sources configured"
    assert {r.state for r in results} <= {"reachable", "unreachable", "needs_key", "rate_limited", "blocked"}
    # The probe must always say something about every source, including why it failed.
    assert all(r.detail for r in results)


def test_at_least_one_official_or_factcheck_source_is_reachable():
    results = probe_all(load_config())
    usable = [r for r in results if r.tier <= 2 and r.state in ("reachable", "blocked", "needs_key")]
    assert usable, "no Tier 1 or 2 source is reachable from this machine; the demo cannot run here"
