"""HTTP surface. It serves what other modules computed; it computes no verdicts (Bible: api serves, never computes).

Start it with:  uvicorn satyasutra.api.app:create_app --factory
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, model_validator

from .. import __version__
from ..config import Config, load_config
from ..envfile import load_env_file
from ..cache import TTLCache
from ..claims import ExtractionResult, extract_claims
from ..errors import ConfigError, SourceError
from ..ingest import author_salt, hash_author
from ..ledger import LedgerStore, SQLiteLedgerStore
from ..llm import GeminiClient
from ..pipeline import verify_post
from ..schemas import SCHEMA_VERSION, Post
from ..schemas.core import sha256_text
from ..sources import FactCheckClient, FactCheckResult, ProbeResult, probe_all
from ..sources.bluesky import PostNotFound, fetch_post

ROOT = Path(__file__).resolve().parents[3]


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
) -> FastAPI:
    load_env_file(ROOT / ".env")
    cfg = config or load_config()
    if ledger is None:
        ledger = SQLiteLedgerStore(_path(cfg.runtime.ledger_db_path), code_version=__version__)

    app = FastAPI(title="SATYASUTRA", version=__version__)
    app.state.config = cfg
    app.state.ledger = ledger
    salt = author_salt()

    # Built on first use, so /health works with no keys set; a missing key then surfaces as a clear error on /verify only.
    @cache
    def gemini() -> GeminiClient:
        return GeminiClient(cfg)

    @cache
    def factcheck() -> FactCheckClient:
        return FactCheckClient(cfg, TTLCache(_path(cfg.runtime.cache_db_path)))

    do_extract = extract or (lambda post: extract_claims(gemini(), cfg, post))
    do_lookup = lookup or (lambda text, lang: factcheck().search(text, lang))
    do_fetch = fetch or (lambda uri: fetch_post(uri, cfg, salt))

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
            done = verify_post(post, cfg, ledger, do_extract, do_lookup)
        except PostNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ConfigError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except SourceError as exc:
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc.detail}")
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
