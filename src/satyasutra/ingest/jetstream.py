"""Live public posts from Bluesky Jetstream, as `Post` records.

parse_event is plain code (no network); stream_posts owns the websocket, reconnects and resumes from the last cursor.
Handles are never kept: the author is a salted hash of the DID (Bible D27). Embedded media is Phase 2.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Callable, Mapping
from urllib.parse import urlencode

import websockets

from ..config import IngestSettings
from ..schemas import Post

MICROS_PER_SECOND = 1_000_000


@dataclass
class IngestStats:
    """Counts of what happened, by reason, so a quiet stream can be told apart from a broken one."""

    events: int = 0
    posts: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    malformed: int = 0
    reconnects: int = 0
    last_cursor_us: int | None = None

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def author_salt(env: Mapping[str, str] | None = None) -> str:
    """FORGE_AUTHOR_SALT keeps author hashes stable across runs. Without it a random per-process salt is used, so hashes
    are not comparable between restarts (and cannot be reversed by anyone holding the data)."""
    env = os.environ if env is None else env
    return env.get("FORGE_AUTHOR_SALT") or secrets.token_hex(16)


def _hash_author(salt: str, did: str) -> str:
    return hashlib.sha256(f"{salt}|{did}".encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_event(
    raw: str | bytes,
    cfg: IngestSettings,
    salt: str,
    stats: IngestStats,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Post | None:
    """One Jetstream message to a Post, or None. Every None is counted in stats, by reason."""
    stats.events += 1
    try:
        event = json.loads(raw)
        kind = event.get("kind")
        cursor = int(event["time_us"])
    except (ValueError, KeyError, TypeError, AttributeError):
        stats.malformed += 1
        return None
    stats.last_cursor_us = cursor

    if kind != "commit":
        stats.skip("not_commit")
        return None
    commit = event.get("commit")
    if not isinstance(commit, dict):
        stats.malformed += 1
        return None
    if commit.get("operation") != "create":
        stats.skip("not_create")
        return None
    if commit.get("collection") not in cfg.collections:
        stats.skip("other_collection")
        return None

    record = commit.get("record")
    did, rkey = event.get("did"), commit.get("rkey")
    if not (isinstance(record, dict) and isinstance(did, str) and isinstance(rkey, str)):
        stats.malformed += 1
        return None
    text = record.get("text")
    if not isinstance(text, str):
        stats.malformed += 1
        return None

    if cfg.skip_replies and "reply" in record:
        stats.skip("reply")
        return None
    if len(text.strip()) < cfg.min_text_chars:
        stats.skip("too_short")
        return None
    langs = [x for x in (record.get("langs") or []) if isinstance(x, str)]
    if cfg.langs and not set(langs) & set(cfg.langs):
        stats.skip("language")
        return None
    if cfg.keywords and not any(k.lower() in text.lower() for k in cfg.keywords):
        stats.skip("keyword")
        return None

    try:
        posted_at = _parse_time(record["createdAt"])
        collection = commit["collection"]
        post = Post(
            id=f"at://{did}/{collection}/{rkey}",
            source_id=cfg.source_id,
            url=cfg.post_url_template.format(did=did, rkey=rkey),
            author_hash=_hash_author(salt, did),
            text=text,
            lang=langs[0] if langs else None,
            posted_at=posted_at,
            fetched_at=now(),
        )
    except (ValueError, KeyError, TypeError):
        stats.malformed += 1
        return None
    stats.posts += 1
    return post


def _url(cfg: IngestSettings, cursor_us: int | None) -> str:
    params: list[tuple[str, str]] = [("wantedCollections", c) for c in cfg.collections]
    if cursor_us is not None:
        params.append(("cursor", str(cursor_us - int(cfg.resume_overlap_s * MICROS_PER_SECOND))))
    return f"{cfg.ws_url}?{urlencode(params)}"


async def stream_posts(
    cfg: IngestSettings,
    stats: IngestStats | None = None,
    salt: str | None = None,
    connect=websockets.connect,
) -> AsyncIterator[Post]:
    """Yield posts forever. A drop, an idle stream or a refused connection reconnects with backoff and resumes from the
    last cursor; the overlap this causes is removed by remembering recent post ids."""
    stats = stats if stats is not None else IngestStats()
    salt = salt or author_salt()
    recent: deque[str] = deque(maxlen=cfg.recent_ids_kept)
    seen: set[str] = set()
    delay = cfg.reconnect_delay_s
    first = True

    while True:
        if not first:
            stats.reconnects += 1
        first = False
        try:
            async with connect(_url(cfg, stats.last_cursor_us), open_timeout=cfg.open_timeout_s) as ws:
                delay = cfg.reconnect_delay_s
                while True:
                    raw = await asyncio.wait_for(ws.recv(), cfg.idle_timeout_s)
                    post = parse_event(raw, cfg, salt, stats)
                    if post is None:
                        continue
                    if post.id in seen:
                        stats.skip("duplicate")
                        stats.posts -= 1
                        continue
                    if len(recent) == recent.maxlen:
                        seen.discard(recent[0])
                    recent.append(post.id)
                    seen.add(post.id)
                    yield post
        except (OSError, asyncio.TimeoutError, websockets.WebSocketException):
            await asyncio.sleep(delay)
            delay = min(delay * 2, cfg.max_reconnect_delay_s)
