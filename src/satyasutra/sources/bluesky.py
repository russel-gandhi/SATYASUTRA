"""Fetch one Bluesky post by its at:// URI from the public AppView, as a Post (the same shape the live stream produces)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import httpx

from ..config import Config
from ..errors import SchemaChanged, Unreachable
from ..ingest.jetstream import hash_author
from ..schemas import Post
from .probe import build_user_agent

NOT_FOUND = "post not found"


class PostNotFound(Exception):
    """The AppView answered but has no such post (deleted, private or a wrong URI)."""


def _post_url(config: Config, did: str, rkey: str) -> str:
    return config.runtime.ingest.post_url_template.format(did=did, rkey=rkey)


def fetch_post(
    uri: str,
    config: Config,
    salt: str,
    client: httpx.Client | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Post:
    settings = config.runtime.verify
    http = client or httpx.Client(timeout=settings.timeout_s, headers={"User-Agent": build_user_agent(config.runtime.probe.user_agent)})
    try:
        response = http.get(settings.posts_endpoint, params={"uris": uri})
    except httpx.HTTPError as exc:
        raise Unreachable("bluesky_public_api", type(exc).__name__) from None
    if response.status_code >= httpx.codes.BAD_REQUEST:
        if response.status_code == httpx.codes.BAD_REQUEST:
            raise PostNotFound(NOT_FOUND)           # the AppView rejects a malformed URI with a 400
        raise Unreachable("bluesky_public_api", f"HTTP {response.status_code}")
    try:
        posts = response.json()["posts"]
    except (ValueError, KeyError, TypeError):
        raise SchemaChanged("bluesky_public_api", "no 'posts' list in the response") from None
    if not posts:
        raise PostNotFound(NOT_FOUND)
    try:
        item = posts[0]
        record = item["record"]
        did = item["author"]["did"]
        rkey = item["uri"].rsplit("/", 1)[-1]
        langs = [x for x in (record.get("langs") or []) if isinstance(x, str)]
        return Post(
            id=item["uri"],
            source_id=config.runtime.ingest.source_id,
            url=_post_url(config, did, rkey),
            author_hash=hash_author(salt, did),
            text=record["text"],
            lang=langs[0] if langs else None,
            posted_at=datetime.fromisoformat(record["createdAt"].replace("Z", "+00:00")).astimezone(timezone.utc),
            fetched_at=now(),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        raise SchemaChanged("bluesky_public_api", "post record is missing fields") from None
