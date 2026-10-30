import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("measure_gemini", ROOT / "scripts" / "measure_gemini.py")
mg = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mg
spec.loader.exec_module(mg)

ERROR_BODY = {
    "error": {
        "code": 429,
        "message": "Quota exceeded",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaMetric": "m", "quotaId": "PerMinute", "quotaValue": "10"}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "21s"},
        ],
    }
}


@pytest.fixture
def server():
    state = {"ok_before_429": 3, "count": 0}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            state["count"] += 1
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if state["count"] > state["ok_before_429"]:
                code, body = 429, ERROR_BODY
            else:
                code, body = 200, {"usageMetadata": {"totalTokenCount": 95}, "candidates": []}
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", state
    srv.shutdown()


def test_parse_quota_error_reads_quota_and_retry_delay():
    p = mg.parse_quota_error(ERROR_BODY["error"])
    assert p["retry_delay"] == "21s" and p["quotas"] == [{"metric": "m", "id": "PerMinute", "value": "10"}]
    assert mg.parse_quota_error({})["quotas"] == []


def test_latency_summary_median_p95_and_tokens():
    samples = [mg.Sample(200, s, tokens=100) for s in (1.0, 2.0, 3.0, 4.0)] + [mg.Sample(0, 60.0)]
    s = mg.latency_summary(samples)
    assert s["n"] == 5 and s["ok"] == 4 and s["median_s"] == 2.5 and s["p95_s"] == 4.0 and s["tokens_per_call"] == 100
    assert s["statuses"] == [0, 200]


def test_budget_cap_is_never_exceeded_under_threads():
    b = mg.Budget(5)
    taken = []
    ts = [threading.Thread(target=lambda: taken.append(b.take())) for _ in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(taken) == 5 and b.used == 5


def test_burst_stops_at_first_429_and_reports_quota(server):
    base, state = server
    budget = mg.Budget(50)
    out = mg.burst(lambda: mg.call(base, "m", "k", 5), budget, workers=1)
    assert out["hit_429"] and out["ok_before_limit"] == 3 and out["quota"]["retry_delay"] == "21s"
    assert budget.used <= 5


def test_burst_without_429_reports_not_limited(server):
    base, state = server
    state["ok_before_429"] = 1000
    out = mg.burst(lambda: mg.call(base, "m", "k", 5), mg.Budget(6), workers=2)
    assert not out["hit_429"] and out["calls"] == 6


def test_timeout_is_status_zero(server):
    s = mg.call("http://127.0.0.1:9", "m", "k", 1)
    assert s.status == 0


def test_render_says_not_measured_when_no_429():
    row = {"model": "m", "latency": mg.latency_summary([mg.Sample(200, 1.0)]), "burst": {"calls": 3, "ok_before_limit": 3, "elapsed_s": 1.0, "rate_per_min": 180, "hit_429": False, "quota": None, "other_statuses": []}}
    text = mg.render([row], {"slow": [mg.Sample(0, 60.0)]}, "T", 40, 3)
    assert "not measured" in text and "| slow | 1 | 0 | 1 |" in text
