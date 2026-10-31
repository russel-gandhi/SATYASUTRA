import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from satyasutra.config import SourceConfig
from satyasutra.errors import RateLimited, Unreachable
from satyasutra.sources import probe_source

SEEN: list[str] = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        SEEN.append(self.path)
        path = urlparse(self.path).path
        if path == "/redirect":
            self.send_response(302); self.send_header("Location", "/ok"); self.end_headers(); return
        code = {"/ok": 200, "/limited": 429, "/forbidden": 403, "/boom": 500, "/missing": 404}.get(path, 200)
        self.send_response(code); self.end_headers(); self.wfile.write(b"x")

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def base():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def client():
    with httpx.Client(timeout=2, follow_redirects=True) as c:
        yield c


def src(url, **kw):
    return SourceConfig(id="t", role="test", probe_url=url, tier=2, **kw)


def test_reachable(base, client):
    r = probe_source(src(base + "/ok"), client)
    assert r.state == "reachable" and r.http_status == 200 and r.latency_ms is not None and r.as_error() is None


def test_redirect_followed(base, client):
    assert probe_source(src(base + "/redirect"), client).state == "reachable"


def test_rate_limited_maps_to_typed_error(base, client):
    r = probe_source(src(base + "/limited"), client)
    assert r.state == "rate_limited" and isinstance(r.as_error(), RateLimited)


def test_forbidden_is_blocked_not_unreachable(base, client):
    r = probe_source(src(base + "/forbidden"), client)
    assert r.state == "blocked" and r.http_status == 403 and r.as_error() is None


def test_server_error_and_404_are_unreachable(base, client):
    for path in ("/boom", "/missing"):
        r = probe_source(src(base + path), client)
        assert r.state == "unreachable" and isinstance(r.as_error(), Unreachable)


def test_connection_refused_is_unreachable_with_class_name_only(client):
    r = probe_source(src("http://127.0.0.1:1/"), client)
    assert r.state == "unreachable" and r.http_status is None and "127.0.0.1" not in r.detail


def test_missing_key_means_needs_key_and_no_request(base, client):
    SEEN.clear()
    r = probe_source(src(base + "/ok", auth_env="FORGE_TEST_PROBE_KEY", auth_param="key"), client, env={})
    assert r.state == "needs_key" and "FORGE_TEST_PROBE_KEY" in r.detail and SEEN == []


def test_key_is_sent_as_param_and_never_reported(base, client):
    SEEN.clear()
    s = src(base + "/ok", auth_env="FORGE_TEST_PROBE_KEY", auth_param="key")
    r = probe_source(s, client, env={"FORGE_TEST_PROBE_KEY": "s3cret-value"})
    assert r.state == "reachable"
    assert parse_qs(urlparse(SEEN[-1]).query)["key"] == ["s3cret-value"]
    assert "s3cret-value" not in repr(r)


def test_auth_env_without_param_still_probes_host(base, client):
    # e.g. a bot token that travels in the path: the probe checks the host only
    assert probe_source(src(base + "/ok", auth_env="FORGE_TEST_TOKEN"), client, env={}).state == "reachable"


def test_key_is_added_without_dropping_the_urls_own_query(base, client):
    SEEN.clear()
    s = src(base + "/ok?query=election&pageSize=1", auth_env="FORGE_TEST_PROBE_KEY", auth_param="key")
    probe_source(s, client, env={"FORGE_TEST_PROBE_KEY": "k"})
    assert parse_qs(urlparse(SEEN[-1]).query) == {"query": ["election"], "pageSize": ["1"], "key": ["k"]}


def test_user_agent_takes_contact_from_env_and_says_so_when_missing():
    from satyasutra.sources.probe import build_user_agent

    t = "FORGE/0 (contact: {contact})"
    assert build_user_agent(t, {"FORGE_CONTACT": " team@example.org "}) == "FORGE/0 (contact: team@example.org)"
    assert build_user_agent(t, {}) == "FORGE/0 (contact: no contact configured)"
