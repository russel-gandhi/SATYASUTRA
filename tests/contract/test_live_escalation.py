"""The escalator on the real stream with the real slow path. Run with `pytest -m contract`.

A short window of general chatter may hold no spreading cluster, so zero escalations is a valid outcome and is accepted;
the floor is never lowered to produce activity. What must hold either way: every escalation has a result or a counted
failure, and the calls to Gemini never exceed the per-minute budget.
"""

import asyncio
import threading
from pathlib import Path

import pytest

from satyasutra import __version__
from satyasutra.cache import TTLCache
from satyasutra.claims.extract import extract_claims
from satyasutra.config import load_config
from satyasutra.envfile import load_env_file
from satyasutra.fastclock import Escalator, FastClock, FastClockWorker
from satyasutra.ingest import hash_author
from satyasutra.ledger import SQLiteLedgerStore
from satyasutra.llm.gemini import GeminiClient
from satyasutra.pipeline import verify_post
from satyasutra.schemas import Post
from satyasutra.sources.factcheck import FactCheckClient

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]
WINDOW_S = 90
TICK_S = 1.0


def test_every_escalation_has_a_result_or_a_counted_failure_and_stays_inside_the_budget(tmp_path):
    load_env_file(ROOT / ".env")
    cfg = load_config()
    cfg = cfg.model_copy(update={"runtime": cfg.runtime.model_copy(update={"escalation": cfg.runtime.escalation.model_copy(update={"tick_s": TICK_S})})})
    gemini, factcheck = GeminiClient(cfg), FactCheckClient(cfg, TTLCache(tmp_path / "fc.sqlite3"))
    ledger = SQLiteLedgerStore(tmp_path / "ledger.sqlite3", code_version=__version__)
    model_calls, lock = [], threading.Lock()

    def extract(post):
        with lock:
            model_calls.append(post.id)
        return extract_claims(gemini, cfg, post)

    def audit(post: Post) -> dict:
        done = verify_post(post, cfg, ledger, extract, lambda text, lang: factcheck.search(text, lang))
        return {"claims": [{"label": r.outcome.verdict.label} for r in done.results]}

    def make_post(cluster) -> Post:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        return Post(id=cluster.rep_post_id, source_id=cfg.runtime.ingest.source_id, url="", author_hash=hash_author("contract", cluster.rep_post_id),
                    text=cluster.rep_text, posted_at=now, fetched_at=now)

    async def go():
        worker = FastClockWorker(cfg, FastClock(cfg, TTLCache(tmp_path / "c.sqlite3")), salt="contract")
        esc = Escalator(cfg, worker, audit, make_post)
        worker.start()
        esc.start()
        await asyncio.sleep(WINDOW_S)
        stats, recent = worker.stats(), esc.recent(cfg.runtime.escalation.max_audits_kept)
        await esc.stop()
        await worker.stop()
        return stats, recent, esc.budget

    stats, recent, budget = asyncio.run(go())
    share = stats["escalation_share"]
    print(f"live {WINDOW_S}s: {stats['posts_processed']} posts, {stats['escalations_started']} escalated "
          f"({stats['escalations_failed']} failed), eligible at end {stats['eligible_now']}, share {share}, Gemini calls {len(model_calls)}")
    for a in recent:
        print(f"  cluster {a['cluster_id']} {a['status']} {a['labels'] or a['error']} at {a['authors_per_minute']} authors/min, {a['members_when_escalated']} members")

    assert stats["error"] == "" and stats["posts_processed"] > 0
    assert len(recent) == stats["escalations_started"]                              # nothing started and then lost
    assert all(a["status"] == "done" or a["error"] for a in recent)                 # a result, or a counted failure with its class
    assert sum(a["status"] == "failed" for a in recent) == stats["escalations_failed"]
    assert len(model_calls) <= budget * (WINDOW_S // 60 + 1)                        # Gemini calls never exceed the budget per minute
    assert len(model_calls) <= stats["escalations_started"]                         # and only escalations spend it
