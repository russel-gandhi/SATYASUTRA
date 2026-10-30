"""HTTP surface. It serves what other modules computed; it computes no verdicts (Bible: api serves, never computes).

Start it with:  uvicorn satyasutra.api.app:create_app --factory
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException

from .. import __version__
from ..config import Config, load_config
from ..ledger import LedgerStore, SQLiteLedgerStore
from ..schemas import SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[3]


def create_app(config: Config | None = None, ledger: LedgerStore | None = None) -> FastAPI:
    cfg = config or load_config()
    if ledger is None:
        db = Path(cfg.runtime.ledger_db_path)
        ledger = SQLiteLedgerStore(db if db.is_absolute() else ROOT / db, code_version=__version__)

    app = FastAPI(title="SATYASUTRA", version=__version__)
    app.state.config = cfg
    app.state.ledger = ledger

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

    return app
