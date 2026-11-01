"""HTTP surface. It serves what other modules computed; it computes no verdicts (Bible: api serves, never computes).

Start it with:  uvicorn satyasutra.api.app:create_app --factory
"""

from __future__ import annotations

import json
from contextvars import ContextVar
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi import Path as Path_
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from .. import __version__
from ..config import Config, config_dir, load_config
from ..envfile import load_env_file
from ..agent import format_sse, trace_events
from ..cache import TTLCache
from ..fastclock import Cluster, Escalator, FastClock, FastClockWorker
from ..media import check_media, forensic_scores
from ..video import check_video
from ..provenance import ImageIndex
from ..agent.graph import BUSY_REASONS, AgentRun, build_claim_agent
from ..claims import ExtractionResult, Grounded, Judgements, OfficialResult, WebResult, check_official, check_web, extract_claims, ground_entities, judge_reviews, judge_with_fallback
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
from ..act import build_pack, build_plan, is_intimate, load_actions
from ..scam import check_links, load_domains
from ..raksha.ask import answer_question, public as raksha_public, route as raksha_route
from ..raksha.corpus import CorpusIndex, load_rag
from ..raksha.drafts import load_drafts, render as render_draft
from ..raksha.flow import load_flow
from ..raksha.ladder import AGES as RAKSHA_AGES, load_ladder, view as ladder_view
from ..scam.script import load_script
from ..scam.session import SessionFull, Sessions
from ..voice import Transcriber, Transcript, VoiceError, second_opinion
from ..audio import SpoofDetector
from ..card import build_card, load_cards
from ..intel import case_view, global_graph, group_claims, media_lab, overview, registry, summarise
from ..inputs import InputError, fetch_page, image_metadata, image_thumb, pdf_text
from ..notice import NoticeError, analyse_text, check_notice
from ..officials import annotate, build_view
from ..pulse import Pulse
from ..sources.index import OfficialIndex
from ..sources.news import check_news, refresh_news_if_stale
from ..sources.official import refresh_if_stale
from ..verdict_cache import VerdictCache, mark
from ..records import RecordStore
from ..report import build_report

ROOT = Path(__file__).resolve().parents[3]
JOB_ID = r"^[A-Za-z0-9_-]+$"
PRIVATE: ContextVar[bool] = ContextVar("private", default=False)
MAX_NON_CLAIMS_SHOWN = 3        # what the model made of a message that held no claim, so the reply can say how it was read


class CardRequest(BaseModel):
    claim: dict


class ActRequest(BaseModel):
    text: str
    ledger_seqs: list[int] = []
    labels: list[str] = []


class ReportRequest(BaseModel):
    record_ids: list[str] = Field(min_length=1)
    lang: str = "en"


class HistoryRequest(BaseModel):
    ids: list[str] = Field(max_length=200)


class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class CaseRecords(BaseModel):
    record_ids: list[str] = Field(min_length=1, max_length=50)


class CaseNote(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class UrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


class GuardText(BaseModel):
    session: str = Field(min_length=8, max_length=64, pattern="^[A-Za-z0-9_-]+$")
    text: str = Field(min_length=1)


class RakshaAsk(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    lang: str = Field(default="en", pattern="^(en|hi)$")


class RakshaChat(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    lang: str = Field(default="en", pattern="^(en|hi)$")
    age: str = Field(default="unsure", pattern="^(adult|minor|unsure)$")
    history: list[str] = Field(default_factory=list, max_length=4)       # the person's own earlier messages, kept by the page, not by us


class RakshaDraft(BaseModel):
    kind: str = Field(pattern="^(complaint|followup|escalation)$")
    lang: str = Field(default="en", pattern="^(en|hi)$")
    age: str = Field(default="unsure", pattern="^(adult|minor|unsure)$")
    what: str = Field(default="shared", pattern="^(threatened|shared|faked)$")
    fields: dict[str, str] = Field(default_factory=dict, max_length=12)


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
    web: Callable[[str], WebResult] | None = None,
    transcribe: Callable[[bytes], Transcript] | None = None,
    audio_check_fn: Callable[[bytes], object] | None = None,
    analyse_image: Callable[[Config, bytes], dict] = forensic_scores,
    image_index: ImageIndex | None = None,
    raksha_ask: Callable[[str, str], dict] | None = None,
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

    class RevalidatePages:
        """Pages and shared files are re-checked on every load (the browser still gets a cheap 304), so an edited page is never shown from a stale copy.
        A plain ASGI wrapper that only touches response headers, so the event streams are not buffered."""
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            async def send_with_header(message):
                if message["type"] == "http.response.start":
                    headers = [(k, v) for k, v in message.get("headers", [])]
                    kind = next((v for k, v in headers if k == b"content-type"), b"")
                    if kind.startswith((b"text/html", b"text/css", b"text/javascript", b"application/javascript")):
                        headers.append((b"cache-control", b"no-cache"))
                    message = {**message, "headers": headers}
                await send(message)
            await self.inner(scope, receive, send_with_header if scope["type"] == "http" else send)

    app.add_middleware(RevalidatePages)

    private_header = cfg.runtime.telegram.private_header.encode()

    class PrivateMode:
        """A request carrying the private header (the Telegram bot sends it on every call) is marked for the whole request, so `keep` writes nothing for it."""
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.inner(scope, receive, send)
            flagged = any(k == private_header and v == b"1" for k, v in scope.get("headers", []))
            token = PRIVATE.set(flagged)
            try:
                await self.inner(scope, receive, send)
            finally:
                PRIVATE.reset(token)

    app.add_middleware(PrivateMode)
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
    do_judge = judge or ((lambda text, found: judge_with_fallback(gemini(), cfg, text, found)) if extract is None else None)
    @cache
    def wikidata() -> WikidataClient:
        return WikidataClient(cfg, TTLCache(_path(cfg.runtime.cache_db_path)))

    do_ground = ground or ((lambda text: ground_entities(gemini(), cfg, text, wikidata().search, wikidata().death_date)) if extract is None else None)
    @cache
    def official_index() -> OfficialIndex:
        return OfficialIndex(_path(cfg.runtime.official.db_path))

    do_web = web or ((lambda text: check_web(gemini(), cfg, text)) if extract is None else None)
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

    fonts = ROOT / "assets" / "fonts"
    if fonts.is_dir():                                                 # the Devanagari font for the correction card (OFL licence file sits beside it)
        app.mount("/assets/fonts", StaticFiles(directory=fonts), name="fonts")

    shared = ROOT / "ui" / "static"
    if shared.is_dir():                                                # nav.js: the one navigation bar every page loads
        app.mount("/static", StaticFiles(directory=shared), name="static")

    @app.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return FileResponse(ROOT / "ui" / "index.html", media_type="text/html")

    @app.get("/check/{kind}", include_in_schema=False)
    def check_page(kind: str = Path_(pattern="^(claim|photo|notice|voice|link)$")) -> FileResponse:
        """One page per kind of check. They share one script; the page reads its own path and shows only that tool."""
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

    @app.post("/notice/check")
    async def notice_check(request: Request) -> dict:
        """Raw image bytes of a notice or message screenshot. Gemini vision copies the text; plain code checks links, wording and the issuer's claim."""
        data = await request.body()

        def run() -> dict:
            refresh_if_stale(cfg, official_index())
            r = check_notice(gemini(), official_index(), data, cfg)
            out = {"text": r.text, "issuer": r.issuer, "urls": r.urls, "phone_numbers": r.phone_numbers, "asks_for": r.asks_for, "findings": r.findings,
                   "official_match": r.official_match, "plan": r.plan}
            return {**out, "record_id": keep("notice", {**out, "name": request.headers.get("x-filename", "")[:120]}),
                    "note": "The text was read from the image by a language model and can contain errors. These findings are not a verdict: nothing here says the notice is genuine or fake."}
        try:
            return await run_in_threadpool(run)
        except NoticeError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    def analysed(text: str, name: str, kind: str, extra: dict | None = None) -> dict:
        refresh_if_stale(cfg, official_index())
        r = analyse_text(official_index(), {"text": text, "issuer": "", "urls": [], "phone_numbers": [], "asks_for": []}, cfg,
                         empty="No readable text was found, so nothing could be checked. Some sites build their text with scripts that we do not run.", issuer_check=kind != "page")
        out = {"text": r.text, "findings": r.findings, "official_match": r.official_match, "plan": r.plan, "name": name, **(extra or {})}
        return {**out, "record_id": keep(kind, out),
                "note": "These findings come from the text as we read it, with word lists and the official-domain list. They are not a verdict: nothing here says the document is genuine or fake."}

    @app.post("/url/check")
    async def url_check(req: UrlRequest) -> dict:
        """Fetch a public web page (http/https only, public addresses only) and run the notice analysis on its text. The UI then sends the text to the claim agent."""
        try:
            page = await run_in_threadpool(fetch_page, cfg, req.url)
            return await run_in_threadpool(analysed, page.text, page.title or page.url, "page", {"url": page.url, "kind": page.kind})
        except InputError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post("/doc/check")
    async def doc_check(request: Request) -> dict:
        """Raw PDF bytes (a circular, a forwarded notice). Its text is read with a PDF reader, then analysed like a notice and sent to the claim agent by the UI."""
        data = await request.body()
        if not data or len(data) > cfg.runtime.inputs.max_bytes:
            raise HTTPException(status_code=422, detail=f"send 1 to {cfg.runtime.inputs.max_bytes} bytes of PDF in the request body")
        try:
            text = (await run_in_threadpool(pdf_text, data, cfg))[: cfg.runtime.inputs.max_text_chars]
            return await run_in_threadpool(analysed, text, request.headers.get("x-filename", "")[:120], "doc", {"kind": "pdf"})
        except InputError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @cache
    def pulse() -> Pulse:
        return Pulse(cfg)

    @app.get("/pulse")
    def pulse_data() -> dict:
        """What India is reading, searching and fact-checking right now, fetched live from Indian outlets, fact-checkers and Google Trends India (cached for refresh_s)."""
        return annotate(pulse().snapshot(), lambda text: (c.body if (c := verdicts().get(text)) else None), cfg)

    @app.get("/pulse/match")
    def pulse_match(text: str = Query(min_length=1, max_length=cfg.runtime.verify.max_text_chars)) -> dict:
        """Is a pasted claim already in the latest Indian headlines or fact-checkers' articles? Context for the reader: it never changes a verdict."""
        return pulse().match(text)

    @app.get("/officials/data")
    def officials_data() -> dict:
        """Spreading clusters with their topic and what we already know about each: a triage list, not a verdict. Disaster topics come first."""
        if worker is None:
            raise HTTPException(status_code=503, detail="the fast-clock worker is not enabled (fastclock.worker_enabled in config/runtime.yaml)")
        rows = build_view(worker.top(), lambda text: (c.body if (c := verdicts().get(text)) else None), cfg)
        return {"rows": rows, "stats": worker.stats(),
                "note": "Only the Bluesky stream we watch is covered, in memory since the server started. A row says what we have already found; it is not a verdict, and topics are matched by word lists."}

    guard_rules = load_script()
    guard_calls = Sessions(guard_rules)

    def guard_reply(call, text: str, session_id: str) -> dict:
        v = call.add(text)
        return {"state": v.state, "signals": v.signals, "scam_type": v.scam_type, "message": guard_rules.states[v.state].model_dump(), "family_alert": guard_rules.family_alert.model_dump() if v.state == "scam" else None, "advice": guard_rules.advice[v.scam_type].model_dump() if v.state == "scam" and v.scam_type in guard_rules.advice else None,
                "heard": call.tail, "note": "Script words only; it cannot know who is calling. It never says a call is safe. Nothing is recorded or stored."}

    @app.post("/guard/chunk")
    async def guard_chunk(request: Request, x_session: str = Header(min_length=8, max_length=64, pattern="^[A-Za-z0-9_-]+$")) -> dict:
        """A few seconds of call audio in, the call's state out. The audio is transcribed and dropped; nothing is written to disk or to any record."""
        data = await request.body()
        if not data or len(data) > guard_rules.settings.max_chunk_bytes:
            raise HTTPException(status_code=422, detail=f"send 1 to {guard_rules.settings.max_chunk_bytes} bytes of audio")
        try:
            t = await run_in_threadpool((transcribe or speech().transcribe), data)
            call = guard_calls.get(x_session)
        except VoiceError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except SessionFull:
            raise HTTPException(status_code=503, detail="Too many calls are being listened to right now. Try again in a minute.")
        return guard_reply(call, t.text[: guard_rules.settings.max_text_chars], x_session)

    @app.post("/guard/text")
    def guard_text(req: GuardText) -> dict:
        """The same rules on typed words (for a quick try without a microphone). Also in memory only."""
        try:
            call = guard_calls.get(req.session)
        except SessionFull:
            raise HTTPException(status_code=503, detail="Too many calls are being listened to right now. Try again in a minute.")
        return guard_reply(call, req.text[: guard_rules.settings.max_text_chars], req.session)

    @app.delete("/guard/{session_id}")
    def guard_end(session_id: str = Path_(pattern="^[A-Za-z0-9_-]{8,64}$")) -> dict:
        guard_calls.end(session_id)
        return {"ended": True}

    @app.get("/guard", include_in_schema=False)
    def guard_page() -> FileResponse:
        return FileResponse(ROOT / "ui" / "guard.html", media_type="text/html")

    @app.get("/officials", include_in_schema=False)
    def officials_page() -> FileResponse:
        return FileResponse(ROOT / "ui" / "officials.html", media_type="text/html")

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
            if t.language_uncertain and transcribe is None:
                t = await run_in_threadpool(second_opinion, t, data, gemini(), cfg)
        except VoiceError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        out = {"text": t.text, "language": t.language, "language_prob": t.language_prob, "language_uncertain": t.language_uncertain,
               "duration_s": t.duration_s, "seconds": t.seconds, "model": t.model, "alternate": t.alternate}
        return {**out, "record_id": keep("voice", out),
                "note": "This is a machine transcript and can contain errors in names and numbers. It reads the words only; it does not tell whether the voice is real or cloned."}

    @app.post("/report/build")
    def report_build(req: ReportRequest) -> dict:
        """Build and keep a Trust Report from records this server produced. Client-supplied evidence is never accepted: only record ids."""
        if len(req.record_ids) > cfg.runtime.report.max_records_per_report:
            raise HTTPException(status_code=422, detail=f"at most {cfg.runtime.report.max_records_per_report} records per report")
        found = [store().get_record(i) for i in dict.fromkeys(req.record_ids)]
        if not all(found):
            raise HTTPException(status_code=404, detail="a record was not found: check the submission again")
        rid = store().add_report(build_report(found, cfg, req.lang))
        return {"id": rid, "url": f"/report/{rid}", "json": f"/report/{rid}.json"}

    @app.get("/report/{rid}.json")
    def report_json(rid: str = Path_(pattern="^[0-9a-f]{16}$")) -> dict:
        r = store().get_report(rid)
        if r is None:
            raise HTTPException(status_code=404, detail="no such report")
        return r

    @app.get("/report/{rid}", include_in_schema=False)
    def report_page(rid: str = Path_(pattern="^[0-9a-f]{16}$")) -> FileResponse:
        return FileResponse(ROOT / "ui" / "report.html", media_type="text/html")

    def all_records() -> list[dict]:
        return store().list_records(cfg.runtime.intel.max_records)

    @app.post("/history")
    def history(req: HistoryRequest) -> dict:
        """A citizen's own history: the browser keeps its record ids and asks for their summaries. There are no accounts, so nobody else's records are listed."""
        found = store().list_records(len(req.ids) or 1, ids=req.ids) if req.ids else []
        return {"items": [summarise(r) for r in found]}

    @app.delete("/records/{rid}")
    def delete_record(rid: str = Path_(pattern="^[0-9a-f]{16}$")) -> dict:
        if not store().delete_record(rid):
            raise HTTPException(status_code=404, detail="no such record")
        return {"deleted": rid, "note": "Reports already built from this record keep their copy; their links stay valid."}

    @app.get("/intel/records")
    def intel_records() -> dict:
        return {"items": [summarise(r) for r in all_records()]}

    @app.get("/intel/overview")
    def intel_overview() -> dict:
        return overview(all_records(), cfg)

    @app.get("/intel/claims")
    def intel_claims() -> dict:
        return {"groups": group_claims(all_records(), cfg), "note": "Same underlying claim = English claim texts that overlap above a policy threshold (not calibrated). Claims in other languages are translated first."}

    @app.get("/intel/media")
    def intel_media() -> dict:
        return {"items": media_lab(all_records())}

    @app.get("/intel/graph")
    def intel_graph() -> dict:
        return global_graph(all_records())

    @app.get("/registry")
    def registry_view() -> dict:
        refresh_if_stale(cfg, official_index())
        return registry(cfg, official_index())

    @app.post("/cases")
    def case_create(req: CaseCreate) -> dict:
        cid = store().save_case({"title": req.title, "record_ids": [], "notes": [], "created_at": datetime.now(timezone.utc).isoformat()})
        return {"id": cid}

    @app.get("/cases")
    def case_list() -> dict:
        return {"cases": [{"id": c["id"], "title": c["title"], "created_at": c["created_at"], "records": len(c["record_ids"]), "notes": len(c["notes"])} for c in store().list_cases()]}

    def load_case(cid: str) -> dict:
        case = store().get_case(cid)
        if case is None:
            raise HTTPException(status_code=404, detail="no such case")
        return case

    @app.get("/cases/{cid}")
    def case_get(cid: str = Path_(pattern="^[0-9a-f]{12}$")) -> dict:
        return case_view(load_case(cid), all_records(), cfg)

    @app.post("/cases/{cid}/records")
    def case_add(req: CaseRecords, cid: str = Path_(pattern="^[0-9a-f]{12}$")) -> dict:
        case = load_case(cid)
        unknown = [i for i in req.record_ids if store().get_record(i) is None]
        if unknown:
            raise HTTPException(status_code=404, detail="a record was not found")
        case["record_ids"] = list(dict.fromkeys([*case["record_ids"], *req.record_ids]))
        store().save_case(case)
        return {"records": len(case["record_ids"])}

    @app.post("/cases/{cid}/notes")
    def case_note(req: CaseNote, cid: str = Path_(pattern="^[0-9a-f]{12}$")) -> dict:
        case = load_case(cid)
        case["notes"].append({"at": datetime.now(timezone.utc).isoformat(), "text": req.text})
        store().save_case(case)
        return {"notes": len(case["notes"])}

    @app.get("/my-investigations", include_in_schema=False)
    def history_page() -> FileResponse:
        return FileResponse(ROOT / "ui" / "history.html", media_type="text/html")

    @app.get("/command", include_in_schema=False)
    def command_page() -> FileResponse:
        return FileResponse(ROOT / "ui" / "command.html", media_type="text/html")

    @app.get("/help", include_in_schema=False)
    def help_page() -> FileResponse:
        """Quiet page for someone whose private pictures are being shared: steps only, no history, no upload, one-tap exit."""
        return FileResponse(ROOT / "ui" / "help.html", media_type="text/html", headers={"Cache-Control": "no-store"})

    @app.get("/raksha/ladder")
    def raksha_ladder(age: str = Query(default="unsure"), lang: str = Query(default="en", pattern="^(en|hi)$")) -> dict:
        """Who to approach next, each step tied to a quoted official passage that is re-checked against the index on every call. Nothing is stored."""
        return ladder_view(load_ladder(), CorpusIndex(load_rag()), age if age in RAKSHA_AGES else "unsure", lang)

    @app.post("/raksha/ask")
    async def raksha_ask_route(req: RakshaAsk) -> dict:
        """A question answered only from the official documents, each sentence with its quote. Out of scope is refused; danger words get the 112 text first."""
        rag, flow = load_rag(), load_flow()
        def run() -> dict:
            if raksha_ask is not None:
                return raksha_ask(req.text, req.lang)
            index = CorpusIndex(rag)
            if not index.ready():
                raise HTTPException(status_code=503, detail="the Raksha document index is not built yet: run scripts/raksha_corpus.py build")
            return raksha_public(answer_question(gemini(), index, rag, req.text, req.lang), rag, req.lang, flow.messages["danger"].model_dump()[req.lang])
        try:
            return await run_in_threadpool(run)
        except ConfigError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except SourceError:
            raise HTTPException(status_code=503, detail="The language model is at its limit right now, so nothing was answered and nothing was guessed. Try again shortly.")

    @app.post("/raksha/chat")
    async def raksha_chat(req: RakshaChat) -> dict:
        """One chat turn. Plain word lists pick the route (danger, ladder, letter, question); only a question reaches the model, and it is answered from the documents with quotes.
        The page keeps the conversation; the server keeps nothing."""
        rag, flow = load_rag(), load_flow()
        kind = raksha_route(req.text, rag)
        if kind == "danger":
            return {"type": "danger", "message": flow.messages["danger"].model_dump()[req.lang]}
        if kind == "ladder":
            return {"type": "ladder", **ladder_view(load_ladder(), CorpusIndex(rag), req.age, req.lang)}
        if kind == "draft":
            return {"type": "draft"}
        def run() -> dict:
            if raksha_ask is not None:
                return raksha_ask(req.text, req.lang)
            index = CorpusIndex(rag)
            if not index.ready():
                raise HTTPException(status_code=503, detail="the Raksha document index is not built yet: run scripts/raksha_corpus.py build")
            context = " ".join(req.history[-rag.chat.history_turns:])
            return raksha_public(answer_question(gemini(), index, rag, req.text, req.lang, context), rag, req.lang, flow.messages["danger"].model_dump()[req.lang])
        try:
            return {"type": "answer", **await run_in_threadpool(run)}
        except ConfigError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except SourceError:
            raise HTTPException(status_code=503, detail="The language model is at its limit right now, so nothing was answered and nothing was guessed. Try again shortly.")

    @app.post("/raksha/draft")
    def raksha_draft(req: RakshaDraft) -> dict:
        """A letter filled from the fields you typed and fixed wording only: no model, nothing stored, nothing sent."""
        try:
            text = render_draft(load_drafts(), req.kind, req.lang, req.fields, req.age, req.what)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {"kind": req.kind, "lang": req.lang, "text": text}

    @app.get("/news/check")
    async def news_check(text: str = Query(min_length=1, max_length=cfg.runtime.verify.max_text_chars)) -> dict:
        """Do major outlets' recent headlines mention this claim? Context only: it never changes a verdict and is not Tier 1 or 2 evidence."""
        def run() -> dict:
            index = OfficialIndex(_path(cfg.runtime.news.db_path))
            refreshed = refresh_news_if_stale(cfg, index)
            r = check_news(cfg, index, text)
            return {"status": r.status, "note": r.note, "outlets_checked": r.outlets_checked, "outlet_names": [f.publisher for f in cfg.runtime.news.feeds], "outlets_with_hits": r.outlets_with_hits,
                    "items_checked": r.items_checked, "oldest": r.oldest, "newest": r.newest,
                    "hits": [{"publisher": h.publisher, "title": h.title, "url": h.url, "coverage": h.coverage,
                              "published_at": h.published_at.isoformat() if h.published_at else None} for h in r.hits],
                    "refreshed": refreshed}
        return await run_in_threadpool(run)

    @cache
    def spoof() -> SpoofDetector:
        return SpoofDetector(cfg, ROOT)

    @app.post("/audio/check")
    async def audio_check(request: Request) -> dict:
        """Raw audio bytes in; per-window scores from a pretrained anti-spoofing model. Experimental: with no calibrated cut it gives no label."""
        data = await request.body()
        if not data or len(data) > cfg.runtime.voice.max_bytes:
            raise HTTPException(status_code=422, detail=f"send 1 to {cfg.runtime.voice.max_bytes} bytes of audio")
        try:
            r = await run_in_threadpool((audio_check_fn or spoof().check), data)
        except VoiceError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        out = {"label": r.label, "headline": r.headline, "reasons": r.reasons, "scores": r.scores, "cut": None if r.cut != r.cut else r.cut, "seconds": r.seconds,
               "lowest": r.lowest, "flagged_windows": r.flagged_windows, "note": r.note}
        return {**out, "record_id": keep("audio", out)}

    @app.post("/card/spec")
    def card_spec(req: CardRequest) -> dict:
        """The words of a shareable correction card for one decided claim (the page draws it). 409 when the verdict is not decided or not confident enough."""
        card = build_card(req.claim, load_cards())
        if card is None:
            raise HTTPException(status_code=409, detail="a card is made only for a decided verdict (false, true or misleading) with medium or high confidence and a source link")
        return card

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
    def store() -> RecordStore:
        return RecordStore(_path(cfg.runtime.report.db_path))

    def refuse_private_topic(text: str) -> None:
        """Text about private images is not fact-checked: a verdict would put it in the ledger, which cannot be edited. It gets guidance only (/act/plan)."""
        if is_intimate(text, load_actions(config_dir() / "actions.yaml")):
            raise HTTPException(status_code=409, detail="this looks like a private-images situation: nothing was checked or stored. Use /act/plan for the next steps")

    def keep(kind: str, body: dict) -> str | None:
        """Save an investigation record. Never for a private request, and never for anything that reads as the private-images topic, whatever the caller says."""
        if PRIVATE.get() or is_intimate(json.dumps(body, default=str, ensure_ascii=False), load_actions(config_dir() / "actions.yaml")):
            return None
        try:
            return store().add_record(kind, body, cfg.runtime.report.max_record_bytes)
        except (ValueError, OSError):
            return None

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
        metadata = image_metadata(data, cfg)
        out = {"width": result.width, "height": result.height, "label": result.label, "headline": result.headline, "reasons": result.reasons,
               "matches": result.matches, "forensics": result.forensics, "explanation": result.explanation, "metadata": metadata}
        record = keep("media", {"name": request.headers.get("x-filename", "")[:120], "result": out, "thumb": image_thumb(data, cfg.runtime.report.thumb_px), "metadata": metadata})
        return {**out, "heatmap_png_b64": heatmap, "record_id": record}

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
        summary = {"duration_s": result.duration_s, "label": result.label, "headline": result.headline, "reasons": result.reasons, "forensics_available": result.forensics_available,
                   "frames": [{"index": f.index, "at_s": f.at_s, "score": f.score, "flagged": f.flagged, "ai_score": f.ai_score, "ai_flagged": f.ai_flagged, "matches": f.matches} for f in result.frames]}
        video_record = keep("video", {"name": request.headers.get("x-filename", "")[:120], "result": summary})
        return {
            "record_id": video_record,
            "duration_s": result.duration_s, "label": result.label, "headline": result.headline, "reasons": result.reasons, "forensics_available": result.forensics_available,
            "frames": [{"index": f.index, "at_s": f.at_s, "score": f.score, "flagged": f.flagged, "ai_score": f.ai_score, "ai_flagged": f.ai_flagged, "thumb_jpeg_b64": f.thumb_jpeg_b64,
                        "matches": f.matches, "heatmap_png_b64": f.heatmap_png_b64, "note": f.note} for f in result.frames],
        }

    @app.get("/agent/stream")
    async def agent_stream(text: str = Query(min_length=1, max_length=cfg.runtime.verify.max_text_chars)) -> StreamingResponse:
        """Run the claim agent on this text and stream its trace as Server-Sent Events, then one `result` event with the verdicts."""
        clean = text.strip()
        if not clean:
            raise HTTPException(status_code=422, detail="text must not be blank")
        refuse_private_topic(clean)
        job, run = uuid.uuid4().hex[:16], AgentRun()
        graph = build_claim_agent(cfg, ledger, text_post, do_extract, do_lookup, do_judge, do_ground, run, do_official, do_web)

        async def body():
            app.state.active_traces += 1
            try:
                async for event in trace_events(graph, {"job": job, "text": clean}, job, cfg.runtime.trace):
                    yield format_sse(event)
                if run.outcome is not None:
                    shown = verdict_body(run.outcome.post, run.outcome, run.grounded)
                    shown["record_id"] = keep("claims", {"text": clean, "body": shown, "spread": None})
                    yield f"event: result\ndata: {json.dumps(shown)}\n\n"
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
            refuse_private_topic(post.text)
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
        done = verify_post(post, cfg, ledger, do_extract, do_lookup, do_judge, do_web, do_official)
        if done.extraction.status == "failed" and done.extraction.reason.startswith(BUSY_REASONS):
            raise HTTPException(status_code=503, detail="The language model is at its rate limit or unreachable right now, so nothing was checked and nothing was guessed. If it is the per-minute limit, try again shortly; if it is the daily allowance, it resets later and waiting a minute will not help.")
        if done.extraction.status in ("blocked", "failed"):
            raise HTTPException(status_code=502, detail=f"claim extraction {done.extraction.status}: {done.extraction.reason}")
        return verdict_body(post, done)

    return app


def verdict_body(post: Post, done, grounded: dict | None = None) -> dict:
    """The JSON a verdict run returns. `grounded` (claim id -> Grounded) adds the knowledge-graph entities when the agent ran."""
    ex = done.extraction
    return {
        "post": {"id": post.id, "url": post.url, "source_id": post.source_id},
        "extraction": {"status": ex.status, "reason": ex.reason, "model": ex.model, "non_claims": len(ex.non_claims), "promoted": len(ex.promoted),
                       "non_claim_items": [{"quote": n.quote, "kind": n.kind, "paraphrase": n.paraphrase_en} for n in ex.non_claims[:MAX_NON_CLAIMS_SHOWN]]},
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
                **({"entities": [{"name": e.name, "id": e.id, "label": e.label, "description": e.description, "url": e.url, "died": e.died, "died_references": e.died_references}
                                 for e in grounded[r.claim.id].entities]} if grounded and r.claim.id in grounded else {}),
            }
            for r in done.results
        ],
    }


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p
