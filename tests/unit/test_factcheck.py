import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from satyasutra.cache import TTLCache
from satyasutra.config import load_config
from satyasutra.errors import ConfigError, QuotaExhausted, RateLimited, SchemaChanged, Unreachable
from satyasutra.schemas.core import sha256_text
from satyasutra.sources.factcheck import FactCheckClient, map_response, normalise_query

CONFIG = Path(__file__).resolve().parents[2] / "config"
KEY = "SECRET-KEY-123"
# Shape captured from a live response on 2026-10-30: claims[] -> claimReview[] with publisher, textualRating, reviewDate, url, title.
BODY = {
    "claims": [{
        "text": "The MMR vaccine causes autism",
        "claimant": "Someone",
        "claimDate": "2026-08-10T00:00:00Z",
        "claimReview": [{
            "publisher": {"name": "FactCheck.org", "site": "factcheck.org"},
            "url": "https://example.org/review/1",
            "title": "False MMR claims",
            "reviewDate": "2026-08-11T00:00:00Z",
            "textualRating": "False",
            "languageCode": "en",
        }],
    }]
}


@pytest.fixture
def server():
    state = {"queue": [], "paths": []}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            state["paths"].append(self.path)
            code, body = state["queue"].pop(0) if len(state["queue"]) > 1 else state["queue"][0]
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_address[1]}/search"
    yield state
    srv.shutdown()


class Clock:
    def __init__(self):
        self.t = 1_000_000.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def make(server, tmp_path, env=None, clock=None):
    cfg = load_config(CONFIG)
    fc = cfg.runtime.factcheck.model_copy(update={"endpoint": server["url"]})
    cfg = cfg.model_copy(update={"runtime": cfg.runtime.model_copy(update={"factcheck": fc})})
    clock = clock or Clock()
    cache = TTLCache(tmp_path / "c.sqlite3", clock=clock)
    client = FactCheckClient(cfg, cache, env={"GOOGLE_FACTCHECK_API_KEY": KEY} if env is None else env, clock=clock, sleep=clock.sleep)
    return client, clock, cfg


def test_maps_a_real_shaped_response_to_valid_evidence(server, tmp_path):
    server["queue"] = [(200, BODY)]
    client, _, cfg = make(server, tmp_path)
    res = client.search("Does MMR cause autism?", language="en")
    ev = res.evidence[0]
    assert len(res.evidence) == 1 and not res.from_cache
    assert (ev.route, ev.role, ev.source_id, ev.tier) == ("factcheck", "context", "factcheck_tools", cfg.source("factcheck_tools").tier)
    assert ev.rating == "False" and ev.publisher == "FactCheck.org" and ev.url == "https://example.org/review/1"
    assert ev.reviewed_claim == "The MMR vaccine causes autism" and ev.reviewed_at.year == 2026
    assert ev.passage_sha256 == sha256_text(ev.passage) and "False" in ev.passage


def test_key_and_language_travel_as_query_params(server, tmp_path):
    server["queue"] = [(200, BODY)]
    client, _, _ = make(server, tmp_path)
    client.search("some claim", language="hi")
    q = parse_qs(urlparse(server["paths"][0]).query)
    assert q["key"] == [KEY] and q["languageCode"] == ["hi"] and q["query"] == ["some claim"] and q["pageSize"] == ["5"]


def test_empty_result_is_a_normal_answer_not_an_error(server, tmp_path):
    server["queue"] = [(200, {})]
    client, _, _ = make(server, tmp_path)
    res = client.search("zzqx nonsense")
    assert res.evidence == [] and res.skipped_reviews == 0


def test_reviews_without_url_or_rating_are_counted_not_cited():
    body = {"claims": [{"text": "c", "claimReview": [{"url": "", "textualRating": "False"}, {"url": "https://x.org/a"}, {"url": "https://x.org/b", "textualRating": "True"}]}]}
    from datetime import datetime, timezone
    ev, skipped = map_response(body, 2, datetime.now(timezone.utc))
    assert [e.url for e in ev] == ["https://x.org/b"] and skipped == 2


@pytest.mark.parametrize("body", [[], {"claims": "x"}, {"claims": ["x"]}, {"claims": [{"claimReview": "x"}]}])
def test_unexpected_shape_raises_schema_changed(body):
    from datetime import datetime, timezone
    with pytest.raises(SchemaChanged):
        map_response(body, 2, datetime.now(timezone.utc))


@pytest.mark.parametrize("code,exc", [(429, RateLimited), (403, QuotaExhausted), (500, Unreachable), (503, Unreachable), (400, SchemaChanged)])
def test_http_failures_map_to_typed_errors_without_the_key(server, tmp_path, code, exc):
    server["queue"] = [(code, {"error": {"message": f"nope {KEY}"}})]
    client, _, _ = make(server, tmp_path)
    with pytest.raises(exc) as info:
        client.search("a claim")
    assert KEY not in str(info.value)


def test_non_json_body_raises_schema_changed(server, tmp_path):
    server["queue"] = [(200, b"<html>not json</html>")]
    client, _, _ = make(server, tmp_path)
    with pytest.raises(SchemaChanged):
        client.search("a claim")


def test_connection_failure_is_unreachable_and_never_echoes_the_key(tmp_path):
    cfg = load_config(CONFIG)
    fc = cfg.runtime.factcheck.model_copy(update={"endpoint": "http://127.0.0.1:9/search", "timeout_s": 1})
    cfg = cfg.model_copy(update={"runtime": cfg.runtime.model_copy(update={"factcheck": fc})})
    clock = Clock()
    client = FactCheckClient(cfg, TTLCache(tmp_path / "c.sqlite3", clock=clock), env={"GOOGLE_FACTCHECK_API_KEY": KEY}, clock=clock, sleep=clock.sleep)
    with pytest.raises(Unreachable) as info:
        client.search("a claim")
    assert KEY not in str(info.value) and KEY not in repr(info.value.__cause__)


def test_missing_key_raises_config_error_naming_the_variable_only(server, tmp_path):
    server["queue"] = [(200, BODY)]
    client, _, _ = make(server, tmp_path, env={})
    with pytest.raises(ConfigError, match="GOOGLE_FACTCHECK_API_KEY"):
        client.search("a claim")
    assert server["paths"] == []


def test_normalisation_makes_equivalent_queries_share_one_network_call(server, tmp_path):
    server["queue"] = [(200, BODY)]
    client, _, _ = make(server, tmp_path)
    first = client.search("Does  MMR cause autism?")
    second = client.search("  does mmr CAUSE autism? ")
    assert len(server["paths"]) == 1 and not first.from_cache and second.from_cache
    assert second.evidence[0].fetched_at == first.evidence[0].fetched_at      # a cached answer keeps its original fetch time
    assert normalise_query(" É  x ") == normalise_query("é x")


def test_cache_expires_after_the_configured_ttl(server, tmp_path):
    server["queue"] = [(200, BODY)]
    client, clock, cfg = make(server, tmp_path)
    client.search("a claim")
    clock.t += cfg.source("factcheck_tools").cache_ttl_s + 1
    again = client.search("a claim")
    assert len(server["paths"]) == 2 and not again.from_cache


def test_failed_calls_are_not_cached(server, tmp_path):
    server["queue"] = [(500, {}), (200, BODY)]
    client, _, _ = make(server, tmp_path)
    with pytest.raises(Unreachable):
        client.search("a claim")
    assert client.search("a claim").evidence and len(server["paths"]) == 2


def test_calls_are_spaced_to_the_configured_rate_limit(server, tmp_path):
    server["queue"] = [(200, {})]
    client, clock, cfg = make(server, tmp_path)
    client.search("first claim")
    client.search("second claim")
    gap = 60 / cfg.source("factcheck_tools").rate_limit_per_min
    assert clock.slept and abs(clock.slept[0] - gap) < 0.01
