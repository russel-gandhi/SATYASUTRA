"""Pure hashing rules for the ledger (Bible Part 11). No I/O here, so the rules are easy to test and audit."""

from __future__ import annotations

import hashlib
import json

GENESIS_HASH = "0" * 64


def canonical_json(payload: dict) -> str:
    """Sorted keys, no spaces, UTF-8 safe: the same payload always serialises to the same bytes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_sha256(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def entry_hash(prev_hash: str, seq: int, kind: str, payload_digest: str, code_version: str) -> str:
    """hash = SHA-256(prev_hash || seq || kind || payload_sha256 || code_version)."""
    joined = "|".join((prev_hash, str(seq), kind, payload_digest, code_version))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
