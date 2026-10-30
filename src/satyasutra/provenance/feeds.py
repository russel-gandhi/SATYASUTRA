"""Read a fact-checker's RSS feed into items (article link, title, date, category tags, image URLs), and download images.

Images are fetched into memory, handed to the caller to hash and discarded. A feed that changes shape raises SchemaChanged.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urljoin

import httpx

from ..errors import SchemaChanged, Unreachable

CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"  # forge: allow-literal XML namespace identifier, not a source
IMG_SRC = re.compile(r"<img\b[^>]*?\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)
SIZE_SUFFIX = re.compile(r"-\d+x\d+(?=\.[A-Za-z0-9]+$)")      # WordPress thumbnail suffix: photo-619x1024.jpeg is a resize of photo.jpeg


@dataclass(frozen=True)
class FeedItem:
    title: str
    link: str
    published: datetime | None
    labels: str
    image_urls: list[str]


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def full_size(url: str) -> str:
    """Prefer the original upload over WordPress's resized copy of it."""
    return SIZE_SUFFIX.sub("", url)


def parse_feed(xml: bytes, source_id: str) -> list[FeedItem]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise SchemaChanged(source_id, "feed is not valid XML") from None
    items = root.findall(".//item")
    if not items and root.find(".//channel") is None:
        raise SchemaChanged(source_id, "no RSS channel in the response")
    out: list[FeedItem] = []
    for item in items:
        link, title = (item.findtext("link") or "").strip(), (item.findtext("title") or "").strip()
        if not link or not title:
            continue
        body = item.findtext(CONTENT) or item.findtext("description") or ""
        seen: dict[str, None] = {}
        for src in IMG_SRC.findall(unescape(body)):
            seen.setdefault(full_size(urljoin(link, src)), None)
        out.append(FeedItem(
            title=title, link=link, published=_date(item.findtext("pubDate")),
            labels=", ".join(c.text.strip() for c in item.findall("category") if c.text and c.text.strip()),
            image_urls=list(seen),
        ))
    return out


def fetch_feed(client: httpx.Client, url: str, source_id: str) -> list[FeedItem]:
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise Unreachable(source_id, type(exc).__name__) from None
    if response.status_code != httpx.codes.OK:
        raise Unreachable(source_id, f"HTTP {response.status_code}")
    return parse_feed(response.content, source_id)


def download_image(client: httpx.Client, url: str, max_bytes: int, source_id: str) -> bytes | None:
    """The image bytes, or None when it is missing, not an image or larger than max_bytes (a skip, not an error)."""
    try:
        with client.stream("GET", url) as response:
            if response.status_code != httpx.codes.OK or not response.headers.get("content-type", "").startswith("image/"):
                return None
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > max_bytes:
                    return None
            return bytes(data)
    except httpx.HTTPError as exc:
        raise Unreachable(source_id, type(exc).__name__) from None
