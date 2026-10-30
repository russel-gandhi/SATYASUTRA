"""Fill the image index from the live fact-checker feeds. Re-running adds only articles and images not seen before.

Images are downloaded into memory, hashed and dropped. One bad feed or image is counted and skipped, so a single outage
does not stop the others; nothing is invented to fill a gap.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from ..config import Config
from ..errors import SourceError
from ..ratelimit import Spacer
from ..sources.probe import build_user_agent
from .feeds import download_image, fetch_feed
from .fingerprint import BadImage, fingerprint
from .index import ImageIndex, Source


@dataclass
class FeedStats:
    source_id: str
    articles: int = 0
    images_seen: int = 0
    added: int = 0
    already_indexed: int = 0
    too_small: int = 0
    not_an_image: int = 0
    failed: int = 0
    error: str = ""


@dataclass
class BuildStats:
    feeds: list[FeedStats] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(f.added for f in self.feeds)


def build_index(
    config: Config,
    index: ImageIndex,
    http: httpx.Client | None = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> BuildStats:
    settings = config.runtime.provenance
    http = http or httpx.Client(timeout=settings.download_timeout_s, headers={"User-Agent": build_user_agent(config.runtime.probe.user_agent)}, follow_redirects=True)
    spacer = Spacer(settings.image_requests_per_minute, clock, sleep)
    stats = BuildStats()
    for feed in settings.feeds:
        fs = FeedStats(feed.source_id)
        stats.feeds.append(fs)
        try:
            items = fetch_feed(http, config.source(feed.source_id).probe_url, feed.source_id)
        except SourceError as exc:
            fs.error = f"{type(exc).__name__}: {exc.detail}"
            continue
        fs.articles = len(items)
        for item in items:
            for url in item.image_urls[: settings.max_images_per_item]:
                fs.images_seen += 1
                if index.has_article_image(item.link, url):
                    fs.already_indexed += 1
                    continue
                spacer.wait()
                try:
                    data = download_image(http, url, settings.max_image_bytes, feed.source_id)
                    if data is None:
                        fs.not_an_image += 1
                        continue
                    fp = fingerprint(data)
                except BadImage:
                    fs.not_an_image += 1
                    continue
                except SourceError:
                    fs.failed += 1
                    continue
                if min(fp.width, fp.height) < settings.min_side_px:
                    fs.too_small += 1
                    continue
                source = Source(url, item.link, item.title, feed.publisher, feed.lang, item.published, item.labels)
                if index.add(fp, source):
                    fs.added += 1
                else:
                    fs.already_indexed += 1
    return stats
