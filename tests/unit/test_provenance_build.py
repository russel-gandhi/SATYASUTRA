"""Feed parsing and index building against a local server that speaks the real feed and image shapes
(WordPress RSS 2.0: content:encoded with escaped HTML, resized image names, category tags; captured from the live feeds on 2026-10-31)."""

import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

from satyasutra.config import load_config
from satyasutra.errors import SchemaChanged
from satyasutra.provenance import ImageIndex, parse_feed
from satyasutra.provenance.build import build_index
from satyasutra.provenance.feeds import full_size

CONFIG = Path(__file__).resolve().parents[2] / "config"


def png(seed, size=(320, 240)) -> bytes:
    coarse = np.random.RandomState(seed).rand(10, 12, 3)
    buf = io.BytesIO()
    Image.fromarray((coarse * 255).astype("uint8")).resize(size, Image.Resampling.BICUBIC).save(buf, "PNG")
    return buf.getvalue()


def feed(base):
    body = (f'&lt;p&gt;Claim.&lt;/p&gt;&lt;img src="{base}/img/one-619x1024.png" /&gt;&lt;img src="/img/tiny.png"&gt;'
            f'&lt;img src="{base}/img/one-619x1024.png"&gt;&lt;img src="{base}/img/missing.png"&gt;&lt;img src="{base}/img/page.html"&gt;')
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>Fact checker</title>
<item><title>Viral photo is old</title><link>{base}/a/viral-photo</link><pubDate>Thu, 29 Oct 2026 12:37:11 +0000</pubDate>
<category>Fact Check</category><category>Politics</category><content:encoded>{body}</content:encoded></item>
<item><title></title><link>{base}/a/no-title</link></item>
</channel></rss>""".encode("utf-8")


@pytest.fixture
def world():
    state = {"hits": [], "feed_status": 200}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            state["hits"].append(self.path)
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            if self.path == "/feed":
                data, ctype, code = feed(base), "application/rss+xml", state["feed_status"]
            elif self.path == "/img/one.png":
                data, ctype, code = png(1), "image/png", 200
            elif self.path == "/img/tiny.png":
                data, ctype, code = png(2, (40, 30)), "image/png", 200
            elif self.path == "/img/page.html":
                data, ctype, code = b"<html></html>", "text/html", 200
            else:
                data, ctype, code = b"", "text/plain", 404
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["base"] = f"http://127.0.0.1:{srv.server_address[1]}"
    yield state
    srv.shutdown()


def config_for(base):
    cfg = load_config(CONFIG)
    first = cfg.runtime.provenance.feeds[0]
    prov = cfg.runtime.provenance.model_copy(update={"feeds": [first]})
    sources = [s.model_copy(update={"probe_url": f"{base}/feed"}) if s.id == first.source_id else s for s in cfg.sources.sources]
    return cfg.model_copy(update={"runtime": cfg.runtime.model_copy(update={"provenance": prov}),
                                  "sources": cfg.sources.model_copy(update={"sources": sources})})


def test_parse_feed_reads_title_date_labels_and_deduplicated_full_size_image_urls(world):
    [item] = parse_feed(feed(world["base"]), "feed_x")
    assert item.title == "Viral photo is old" and item.labels == "Fact Check, Politics" and item.published.year == 2026
    assert item.image_urls[0] == f"{world['base']}/img/one.png"          # the -619x1024 resize suffix is removed
    assert len(item.image_urls) == len(set(item.image_urls))
    assert f"{world['base']}/img/tiny.png" in item.image_urls              # a relative src is resolved against the article link


def test_full_size_only_strips_a_dimension_suffix_before_the_extension():
    assert full_size("https://x.org/a/photo-619x1024.jpeg") == "https://x.org/a/photo.jpeg"
    assert full_size("https://x.org/a/photo-2026.jpeg") == "https://x.org/a/photo-2026.jpeg"


def test_a_page_that_is_not_a_feed_is_schema_changed():
    with pytest.raises(SchemaChanged):
        parse_feed(b"<html><body>not rss</body></html>", "feed_x")
    with pytest.raises(SchemaChanged):
        parse_feed(b"not xml at all", "feed_x")


def test_build_indexes_real_sized_images_skips_small_missing_and_non_images_and_reruns_add_nothing(world, tmp_path):
    cfg = config_for(world["base"])
    index = ImageIndex(tmp_path / "p.sqlite3")
    with httpx.Client() as http:
        stats = build_index(cfg, index, http, sleep=lambda s: None)
        [fs] = stats.feeds
        assert (fs.articles, fs.added, fs.too_small, fs.not_an_image) == (1, 1, 1, 2) and index.count() == 1
        again = build_index(cfg, index, http, sleep=lambda s: None)
    assert again.added == 0 and again.feeds[0].already_indexed >= 1 and index.count() == 1


def test_a_feed_that_fails_is_reported_and_does_not_stop_the_run(world, tmp_path):
    world["feed_status"] = 503
    cfg = config_for(world["base"])
    with httpx.Client() as http:
        stats = build_index(cfg, ImageIndex(tmp_path / "p.sqlite3"), http, sleep=lambda s: None)
    assert stats.added == 0 and "Unreachable" in stats.feeds[0].error


def test_image_downloads_are_spaced_to_the_configured_rate(world, tmp_path):
    cfg = config_for(world["base"])
    now, slept = [1000.0], []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    with httpx.Client() as http:
        build_index(cfg, ImageIndex(tmp_path / "p.sqlite3"), http, clock=lambda: now[0], sleep=sleep)
    gap = 60 / cfg.runtime.provenance.image_requests_per_minute
    assert len(slept) >= 2 and all(abs(s - gap) < 1e-6 for s in slept)
