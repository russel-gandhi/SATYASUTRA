"""HTTP surface. It serves what other modules computed; it computes no verdicts (Bible: api serves, never computes).

Start it with:  uvicorn satyasutra.api.app:create_app --factory
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, model_validator

from .. import __version__
from ..config import Config, load_config
from ..envfile import load_env_file
from ..agent import format_sse, trace_events
from ..cache import TTLCache
from ..fastclock import Cluster, Escalator, FastClock, FastClockWorker
from ..claims import ExtractionResult, Judgements, extract_claims, judge_reviews
from ..errors import ConfigError, SourceError
from ..ingest import author_salt, hash_author
from ..ledger import LedgerStore, SQLiteLedgerStore
from ..llm import GeminiClient
from ..pipeline import verify_post
from ..schemas import SCHEMA_VERSION, Post
from ..schemas.core import sha256_text
from ..sources import FactCheckClient, FactCheckResult, ProbeResult, probe_all
from ..sources.bluesky import PostNotFound, fetch_post
from ..verdict_cache import VerdictCache, mark

ROOT = Path(__file__).resolve().parents[3]
JOB_ID = r"^[A-Za-z0-9_-]+$"


class VerifyRequest(BaseModel):
    text: str | None = None
    uri: str | None = None

    @model_validator(mode="after")
    def _one_of(self) -> "VerifyRequest":
        if (self.text is None) == (self.uri is None):
            raise ValueError("send exactly one of 'text' or 'uri'")
        return self


def create_app(
    config: Config | None = None,
    ledger: LedgerStore | None = None,
    prober: Callable[[Config], list[ProbeResult]] = probe_all,
    extract: Callable[[Post], ExtractionResult] | None = None,
    lookup: Callable[[str, str | None], FactCheckResult] | None = None,
    fetch: Callable[[str], Post] | None = None,
    trace_graph: Any = None,
    fastclock: FastClock | None = None,
    verdict_cache: VerdictCache | None = None,
    worker: FastClockWorker | None = None,
    escalator: Escalator | None = None,
    judge: Callable[[str, list], Judgements] | None = None,
) -> FastAPI:
    load_env_file(ROOT / ".env")
    cfg = config or load_config()
    if ledger is None:
        ledger = SQLiteLedgerStore(_path(cfg.runtime.ledger_db_path), code_version=__version__)

    salt = author_salt()
    if worker is None and cfg.runtime.fastclock.worker_enabled:
        worker = FastClockWorker(cfg, fastclock or FastClock(cfg, TTLCache(_path(cfg.runtime.cache_db_path))), salt)
    if worker is not None:
        fastclock = fastclock or worker.fastclock          # /verify and the stream feed the same clusters

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if worker is not None:
            worker.start()
        if escalator is not None:
            escalator.start()
        try:
            yield
        finally:
            if escalator is not None:
                await escalator.stop()
            if worker is not None:
                await worker.stop()

    app = FastAPI(title="SATYASUTRA", version=__version__, lifespan=lifespan)
    app.state.config = cfg
    app.state.ledger = ledger
    app.state.worker = worker
    app.state.active_traces = 0           # streams currently open; the disconnect test watches it return to zero

    # Built on first use, so /health works with no keys set; a missing key then surfaces as a clear error on /verify only.
    @cache
    def gemini() -> GeminiClient:
        return GeminiClient(cfg)

    @cache
    def factcheck() -> FactCheckClient:
        return FactCheckClient(cfg, TTLCache(_path(cfg.runtime.cache_db_path)))

    # Built on first use like the clients above; the cache lives in its own file so clearing it never touches the ledger.
    @cache
    def verdicts() -> VerdictCache:
        return verdict_cache or VerdictCache(TTLCache(_path(cfg.runtime.cache_db_path)), cfg, __version__, scope=str(getattr(ledger, "path", "")))

    do_extract = extract or (lambda post: extract_claims(gemini(), cfg, post))
    # The real judge needs Gemini, so it is on only when the real extractor is: callers that inject their own extract stay offline.
    do_judge = judge or ((lambda text, found: judge_reviews(gemini(), cfg, text, found)) if extract is None else None)
    do_lookup = lookup or (lambda text, lang: factcheck().search(text, lang))
    do_fetch = fetch or (lambda uri: fetch_post(uri, cfg, salt))

    def audit_cluster(post: Post) -> dict:
        """The escalator's audit is the /verify path itself, so a cluster's result lands in the verdict cache and the ledger."""
        return mark(verdicts().get_or_compute(post.text, lambda: _run(post)))

    def cluster_post(cluster: Cluster) -> Post:
        now = datetime.now(timezone.utc)
        return Post(id=cluster.rep_post_id, source_id=cfg.runtime.ingest.source_id, url=worker.post_url(cluster.rep_post_id) or "",
                    author_hash=hash_author(salt, cluster.rep_post_id), text=cluster.rep_text, posted_at=now, fetched_at=now)

    if escalator is None and worker is not None and cfg.runtime.escalation.enabled:
        escalator = Escalator(cfg, worker, audit_cluster, cluster_post)
    app_escalator = escalator

    @app.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return FileResponse(ROOT / "ui" / "index.html", media_type="text/html")

    @app.get("/health")
    def health() -> dict:
        head = ledger.head()
        return {
            "status": "ok",
            "version": __version__,
            "schema_version": SCHEMA_VERSION,
            "config_checksum": cfg.checksum,
            "ledger_entries": head.seq if head else 0,
        }

    @app.get("/health/sources")
    def health_sources() -> list[dict]:
        """Live reachability of every configured source, probed now. Slow by design: it is the truth, not a cache."""
        return [r.as_dict() for r in prober(cfg)]

    # Declared before /ledger/{seq} so "verify" is never read as a sequence number.
    @app.get("/ledger/verify")
    def ledger_verify() -> dict:
        result = ledger.verify()
        return {"ok": result.ok, "checked": result.checked, "first_bad_seq": result.first_bad_seq, "reason": result.reason}

    @app.get("/ledger/{seq}")
    def ledger_entry(seq: int) -> dict:
        entry = ledger.get(seq)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no ledger entry {seq}")
        return entry.model_dump(mode="json")

    @app.get("/fastclock/top")
    def fastclock_top() -> dict:
        if worker is None:
            raise HTTPException(status_code=503, detail="the fast-clock worker is not enabled (fastclock.worker_enabled in config/runtime.yaml)")
        return {"clusters": worker.top()}

    @app.get("/fastclock/stats")
    def fastclock_stats() -> dict:
        if worker is None:
            raise HTTPException(status_code=503, detail="the fast-clock worker is not enabled (fastclock.worker_enabled in config/runtime.yaml)")
        return worker.stats()

    @app.get("/fastclock/escalations")
    def fastclock_escalations(n: int = Query(default=20, ge=1, le=200)) -> dict:
        if app_escalator is None:
            raise HTTPException(status_code=503, detail="escalation is not enabled (escalation.enabled in config/runtime.yaml)")
        return {**app_escalator.stats(), "recent": app_escalator.recent(n)}

    @app.get("/trace/stream")
    async def trace_stream(job: str = Query(min_length=1, max_length=64, pattern=JOB_ID)) -> StreamingResponse:
        """Run the agent graph for this job and stream its trace as Server-Sent Events, each event sent as it happens.
        The run lives only as long as the connection: a client that leaves cancels it. (Persisted jobs come with the real agent.)"""
        if trace_graph is None:
            raise HTTPException(status_code=503, detail="no agent graph is configured")

        async def body():
            app.state.active_traces += 1
            try:
                async for event in trace_events(trace_graph, {"job": job}, job, cfg.runtime.trace):
                    yield format_sse(event)
            finally:
                app.state.active_traces -= 1

        return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/verify")
    def verify(request: VerifyRequest) -> dict:
        settings = cfg.runtime.verify
        try:
            if request.uri is not None:
                if not request.uri.startswith(settings.uri_prefix):
                    raise HTTPException(status_code=422, detail=f"uri must start with {settings.uri_prefix}")
                post = do_fetch(request.uri)
            else:
                text = request.text.strip()
                if not text or len(text) > settings.max_text_chars:
                    raise HTTPException(status_code=422, detail=f"text must be 1 to {settings.max_text_chars} characters")
                now = datetime.now(timezone.utc)
                post = Post(id=f"{settings.text_source_id}:{sha256_text(text)[:16]}", source_id=settings.text_source_id, url="",
                            author_hash=hash_author(salt, settings.text_source_id), text=text, posted_at=now, fetched_at=now)
            if fastclock is not None:
                fastclock.process(post)            # a repeat is still a post that spread: it counts toward the cluster's velocity
            cached = verdicts().get_or_compute(post.text, lambda: _run(post))
        except PostNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ConfigError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except SourceError as exc:
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc.detail}")
        return mark(cached)

    def _run(post: Post) -> dict:
        done = verify_post(post, cfg, ledger, do_extract, do_lookup, do_judge)
        ex = done.extraction
        if ex.status in ("blocked", "failed"):
            raise HTTPException(status_code=502, detail=f"claim extraction {ex.status}: {ex.reason}")
        return {
            "post": {"id": post.id, "url": post.url, "source_id": post.source_id},
            "extraction": {"status": ex.status, "reason": ex.reason, "model": ex.model, "non_claims": len(ex.non_claims)},
            "claims": [
                {
                    "claim_id": r.claim.id, "text": r.claim.text_en, "quote": r.claim.text_orig, "lang": r.claim.lang,
                    "label": r.outcome.verdict.label, "confidence_band": r.outcome.verdict.confidence_band,
                    "flags": r.outcome.verdict.flags, "reasons": r.outcome.reasons,
                    "citations": [e.model_dump(mode="json") for e in r.outcome.verdict.evidence],
                    "related": [{"url": a.evidence.url, "publisher": a.evidence.publisher, "rating": a.evidence.rating,
                                 "reviewed_claim": a.evidence.reviewed_claim, "relevance": round(a.relevance, 2), "why_not_used": a.reason}
                                for a in r.outcome.related],
                    "ledger_seq": r.ledger_seq,
                }
                for r in done.results
            ],
        }

    return app


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p
