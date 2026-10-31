"""HTTP surface. It serves what other modules computed; it computes no verdicts (Bible: api serves, never computes).

Start it with:  uvicorn satyasutra.api.app:create_app --factory
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, model_validator

from .. import __version__
from ..config import Config, config_dir, load_config
from ..envfile import load_env_file
from ..agent import format_sse, trace_events
from ..cache import TTLCache
from ..fastclock import Cluster, Escalator, FastClock, FastClockWorker
from ..media import check_media, forensic_scores
from ..video import check_video
from ..provenance import ImageIndex
from ..agent.graph import AgentRun, build_claim_agent
from ..claims import ExtractionResult, Grounded, Judgements, OfficialResult, check_official, extract_claims, ground_entities, judge_reviews
from ..errors import ConfigError, SourceError
from ..ingest import author_salt, hash_author
from ..ledger import LedgerStore, SQLiteLedgerStore
from ..llm import GeminiClient
from ..pipeline import verify_post
from ..schemas import SCHEMA_VERSION, Post
from ..schemas.core import sha256_text
from ..sources import FactCheckClient, FactCheckResult, ProbeResult, probe_all
from ..sources import WikidataClient
from ..sources.bluesky import PostNotFound, fetch_post
from ..act import build_pack, build_plan, load_actions
from ..scam import check_links, load_domains
from ..voice import Transcriber, Transcript, VoiceError
from ..sources.index import OfficialIndex
from ..sources.news import check_news, refresh_news_if_stale
from ..verdict_cache import VerdictCache, mark

ROOT = Path(__file__).resolve().parents[3]
JOB_ID = r"^[A-Za-z0-9_-]+$"


class ActRequest(BaseModel):
    text: str
    ledger_seqs: list[int] = []
    labels: list[str] = []


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
    ground: Callable[[str], Grounded] | None = None,
    official: Callable[[str], OfficialResult] | None = None,
    transcribe: Callable[[bytes], Transcript] | None = None,
    analyse_image: Callable[[Config, bytes], dict] = forensic_scores,
    image_index: ImageIndex | None = None,
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
    @cache
    def wikidata() -> WikidataClient:
        return WikidataClient(cfg, TTLCache(_path(cfg.runtime.cache_db_path)))

    do_ground = ground or ((lambda text: ground_entities(gemini(), cfg, text, wikidata().search)) if extract is None else None)
    @cache
    def official_index() -> OfficialIndex:
        return OfficialIndex(_path(cfg.runtime.official.db_path))

    do_official = official or ((lambda text: check_official(gemini(), cfg, official_index(), text)) if extract is None else None)
    @cache
    def link_domains():
        return load_domains(config_dir() / "official_domains.yaml")

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

    def text_post(text: str) -> Post:
        settings = cfg.runtime.verify
        now = datetime.now(timezone.utc)
        return Post(id=f"{settings.text_source_id}:{sha256_text(text)[:16]}", source_id=settings.text_source_id, url="",
                    author_hash=hash_author(salt, settings.text_source_id), text=text, posted_at=now, fetched_at=now)

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

    @app.get("/links/check")
    def links_check(text: str = Query(min_length=1, max_length=cfg.runtime.verify.max_text_chars)) -> dict:
        """Name-only check of every link in the text against the official-domain list. Links are never opened."""
        found = check_links(text, link_domains())
        return {"links": [{"link": f.link, "host": f.host, "status": f.status, "reason": f.reason, "real_domain": f.real_domain, "owner": f.owner} for f in found],
                "checked_on": link_domains().checked_on,
                "note": "Only the name is checked; the link is never opened. A link that is not in our list is not thereby safe."}

    @cache
    def speech() -> Transcriber:
        return Transcriber(cfg, ROOT)

    @app.post("/voice/transcribe")
    async def voice_transcribe(request: Request) -> dict:
        """Raw audio bytes in, the transcript and detected language out. The caller then checks the text like any typed claim."""
        data = await request.body()
        try:
            t = await run_in_threadpool((transcribe or speech().transcribe), data)
        except VoiceError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {"text": t.text, "language": t.language, "language_prob": t.language_prob, "language_uncertain": t.language_uncertain,
                "duration_s": t.duration_s, "seconds": t.seconds, "model": t.model,
                "note": "This is a machine transcript and can contain errors in names and numbers. It reads the words only; it does not tell whether the voice is real or cloned."}

    @app.get("/news/check")
    async def news_check(text: str = Query(min_length=1, max_length=cfg.runtime.verify.max_text_chars)) -> dict:
        """Do major outlets' recent headlines mention this claim? Context only: it never changes a verdict and is not Tier 1 or 2 evidence."""
        def run() -> dict:
            index = OfficialIndex(_path(cfg.runtime.news.db_path))
            refreshed = refresh_news_if_stale(cfg, index)
            r = check_news(cfg, index, text)
            return {"status": r.status, "note": r.note, "outlets_checked": r.outlets_checked, "outlets_with_hits": r.outlets_with_hits,
                    "items_checked": r.items_checked, "oldest": r.oldest, "newest": r.newest,
                    "hits": [{"publisher": h.publisher, "title": h.title, "url": h.url, "coverage": h.coverage,
                              "published_at": h.published_at.isoformat() if h.published_at else None} for h in r.hits],
                    "refreshed": refreshed}
        return await run_in_threadpool(run)

    @app.post("/act/plan")
    def act_plan(req: ActRequest) -> dict:
        """The next steps for a forward, and a plain-text evidence pack the person takes to the reporting portal. Nothing is filed or sent."""
        text = req.text.strip()
        if not text or len(text) > cfg.runtime.verify.max_text_chars:
            raise HTTPException(status_code=422, detail=f"text must be 1 to {cfg.runtime.verify.max_text_chars} characters")
        actions = load_actions(config_dir() / "actions.yaml")
        links = check_links(text, link_domains())
        plan = build_plan(text, links, req.labels, actions)
        pack = build_pack(text, plan, links, req.ledger_seqs[:cfg.runtime.verify.judge_max_reviews], ledger, actions)
        return {"intent": plan.intent, "why": plan.why, "routes": plan.routes, "notice": plan.notice, "pack": pack}

    @app.get("/fastclock/cluster/{cluster_id}")
    def fastclock_cluster(cluster_id: int) -> dict:
        if worker is None:
            raise HTTPException(status_code=503, detail="the fast-clock worker is not enabled (fastclock.worker_enabled in config/runtime.yaml)")
        found = worker.trace(cluster_id)
        if found is None:
            raise HTTPException(status_code=404, detail="no such live cluster: it may have gone idle and been forgotten")
        return found

    @app.get("/fastclock/match")
    def fastclock_match(text: str = Query(min_length=1, max_length=cfg.runtime.verify.max_text_chars)) -> dict:
        if worker is None:
            raise HTTPException(status_code=503, detail="the fast-clock worker is not enabled (fastclock.worker_enabled in config/runtime.yaml)")
        return {"match": worker.match(text)}

    @app.get("/fastclock/escalations")
    def fastclock_escalations(n: int = Query(default=20, ge=1, le=200)) -> dict:
        if app_escalator is None:
            raise HTTPException(status_code=503, detail="escalation is not enabled (escalation.enabled in config/runtime.yaml)")
        return {**app_escalator.stats(), "recent": app_escalator.recent(n)}

    @cache
    def images() -> ImageIndex:
        return image_index or ImageIndex(_path(cfg.runtime.provenance.db_path))

    @app.post("/media/check")
    async def media_check(request: Request) -> dict:
        """Raw image bytes in the body. Returns provenance matches and TruFor's manipulation read; see satyasutra/media.py."""
        limit = cfg.runtime.forensics.max_upload_bytes
        data = await request.body()
        if not data or len(data) > limit:
            raise HTTPException(status_code=422, detail=f"send 1 to {limit} bytes of image in the request body")
        try:
            result = await run_in_threadpool(check_media, cfg, data, images(), analyse_image)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"not an image: {exc}")
        heatmap = result.forensics.pop("heatmap_png_b64", None)
        return {"width": result.width, "height": result.height, "label": result.label, "reasons": result.reasons,
                "matches": result.matches, "forensics": result.forensics, "heatmap_png_b64": heatmap, "explanation": result.explanation}

    @app.post("/video/check")
    async def video_check(request: Request) -> dict:
        """Raw video bytes in the body. Samples frames and returns per-frame scores; see satyasutra/video.py for what it cannot detect."""
        limit = cfg.runtime.forensics.max_video_bytes
        data = await request.body()
        if not data or len(data) > limit:
            raise HTTPException(status_code=422, detail=f"send 1 to {limit} bytes of video in the request body")
        try:
            result = await run_in_threadpool(check_video, cfg, data, images(), analyse_image)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return {
            "duration_s": result.duration_s, "label": result.label, "reasons": result.reasons, "forensics_available": result.forensics_available,
            "frames": [{"index": f.index, "at_s": f.at_s, "score": f.score, "flagged": f.flagged, "thumb_jpeg_b64": f.thumb_jpeg_b64,
                        "matches": f.matches, "heatmap_png_b64": f.heatmap_png_b64, "note": f.note} for f in result.frames],
        }

    @app.get("/agent/stream")
    async def agent_stream(text: str = Query(min_length=1, max_length=cfg.runtime.verify.max_text_chars)) -> StreamingResponse:
        """Run the claim agent on this text and stream its trace as Server-Sent Events, then one `result` event with the verdicts."""
        clean = text.strip()
        if not clean:
            raise HTTPException(status_code=422, detail="text must not be blank")
        job, run = uuid.uuid4().hex[:16], AgentRun()
        graph = build_claim_agent(cfg, ledger, text_post, do_extract, do_lookup, do_judge, do_ground, run, do_official)

        async def body():
            app.state.active_traces += 1
            try:
                async for event in trace_events(graph, {"job": job, "text": clean}, job, cfg.runtime.trace):
                    yield format_sse(event)
                if run.outcome is not None:
                    yield f"event: result\ndata: {json.dumps(verdict_body(run.outcome.post, run.outcome, run.grounded))}\n\n"
            finally:
                app.state.active_traces -= 1

        return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
                post = text_post(text)
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
        if done.extraction.status in ("blocked", "failed"):
            raise HTTPException(status_code=502, detail=f"claim extraction {done.extraction.status}: {done.extraction.reason}")
        return verdict_body(post, done)

    return app


def verdict_body(post: Post, done, grounded: dict | None = None) -> dict:
    """The JSON a verdict run returns. `grounded` (claim id -> Grounded) adds the knowledge-graph entities when the agent ran."""
    ex = done.extraction
    return {
        "post": {"id": post.id, "url": post.url, "source_id": post.source_id},
        "extraction": {"status": ex.status, "reason": ex.reason, "model": ex.model, "non_claims": len(ex.non_claims)},
        "claims": [
            {
                "claim_id": r.claim.id, "text": r.claim.text_en, "quote": r.claim.text_orig, "lang": r.claim.lang,
                "label": r.outcome.verdict.label, "confidence_band": r.outcome.verdict.confidence_band,
                "flags": r.outcome.verdict.flags, "reasons": r.outcome.reasons, "explanation": r.outcome.explanation,
                "citations": [e.model_dump(mode="json") for e in r.outcome.verdict.evidence],
                "related": [{"url": a.evidence.url, "publisher": a.evidence.publisher, "rating": a.evidence.rating,
                             "reviewed_claim": a.evidence.reviewed_claim, "relevance": round(a.relevance, 2), "why_not_used": a.reason}
                            for a in r.outcome.related],
                "ledger_seq": r.ledger_seq,
                **({"entities": [{"name": e.name, "id": e.id, "label": e.label, "description": e.description, "url": e.url}
                                 for e in grounded[r.claim.id].entities]} if grounded and r.claim.id in grounded else {}),
            }
            for r in done.results
        ],
    }


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p
