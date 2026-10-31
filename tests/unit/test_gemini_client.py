import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from satyasutra.config import load_config
from satyasutra.errors import AuthRefused, ConfigError, ContentBlocked, QuotaExhausted, RateLimited, SchemaChanged, Unreachable
from satyasutra.llm import BadOutput, GeminiClient
from satyasutra.llm.gemini import is_daily_quota, retry_delay_s

CONFIG = Path(__file__).resolve().parents[2] / "config"
KEY = "SECRET-GEMINI-KEY"
SCHEMA = {"type": "OBJECT", "properties": {"x": {"type": "STRING"}}}


def ok(payload: dict | str = None, tokens=95):
    text = payload if isinstance(payload, str) else json.dumps(payload if payload is not None else {"x": "y"})
    # Shape captured from live calls on 2026-10-30: candidates[0].content.parts[0].text and usageMetadata.totalTokenCount
    return 200, {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}], "usageMetadata": {"totalTokenCount": tokens}}


def too_many(delay="2s", quota_id="GenerateRequestsPerMinutePerProjectPerModel-FreeTier"):
    return 429, {"error": {"code": 429, "message": f"Quota exceeded {KEY}", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id, "quotaValue": "15"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay},
    ]}}


@pytest.fixture
def server():
    state = {"queue": [], "requests": [], "delay": 0.0}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            state["requests"].append({"path": self.path, "body": json.loads(self.rfile.read(n) or b"{}")})
            time.sleep(state["delay"])
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
    state["base"] = f"http://127.0.0.1:{srv.server_address[1]}/v1beta"
    yield state
    srv.shutdown()


def make(server, env=None, http=None, **policy):
    cfg = load_config(CONFIG)
    policy.setdefault("models", cfg.policy.gemini.models[:1])          # one model unless a test is about the fallback
    gem = cfg.policy.gemini.model_copy(update={"api_base": server["base"], **policy})
    cfg = cfg.model_copy(update={"policy": cfg.policy.model_copy(update={"gemini": gem})})
    sleeps = []
    client = GeminiClient(cfg, client=http, env={"GEMINI_API_KEY": KEY} if env is None else env, sleep=sleeps.append)
    return client, sleeps, cfg


def test_success_returns_parsed_json_with_model_tokens_and_the_request_shape(server):
    server["queue"] = [ok({"x": "hello"})]
    client, _, cfg = make(server)
    reply = client.generate_json("say hi", SCHEMA, system="be brief")
    assert reply.data == {"x": "hello"} and reply.tokens == 95 and reply.model == cfg.policy.gemini.models[0] and reply.attempts == 1
    req = server["requests"][0]
    assert req["path"].startswith(f"/v1beta/models/{reply.model}:generateContent?key=")
    assert req["body"]["generationConfig"]["responseSchema"] == SCHEMA and req["body"]["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert req["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_429_with_a_short_retry_delay_waits_that_long_and_retries(server):
    server["queue"] = [too_many("2s"), ok()]
    client, sleeps, _ = make(server, requests_per_minute=None)
    reply = client.generate_json("p", SCHEMA)
    assert reply.attempts == 2 and 2.0 in sleeps and len(server["requests"]) == 2


def test_429_with_a_long_retry_delay_is_reported_not_waited_out(server):
    server["queue"] = [too_many("120s")]
    client, sleeps, _ = make(server, requests_per_minute=None)
    with pytest.raises(RateLimited) as info:
        client.generate_json("p", SCHEMA)
    assert len(server["requests"]) == 1 and not sleeps and KEY not in str(info.value)


def test_429_gives_up_after_max_retries(server):
    server["queue"] = [too_many("1s")]
    client, _, _ = make(server, requests_per_minute=None, max_retries=2)
    with pytest.raises(RateLimited):
        client.generate_json("p", SCHEMA)
    assert len(server["requests"]) == 3          # first try plus two retries


def test_daily_quota_is_exhausted_not_retried(server):
    server["queue"] = [too_many("1s", quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier")]
    client, _, _ = make(server, requests_per_minute=None)
    with pytest.raises(QuotaExhausted):
        client.generate_json("p", SCHEMA)
    assert len(server["requests"]) == 1


@pytest.mark.parametrize("code,exc", [(401, AuthRefused), (403, AuthRefused), (404, SchemaChanged), (400, SchemaChanged), (500, Unreachable), (503, Unreachable)])
def test_http_failures_map_to_typed_errors_without_the_key(server, code, exc):
    server["queue"] = [(code, {"error": {"message": f"nope {KEY}"}})]
    client, _, _ = make(server, requests_per_minute=None)
    with pytest.raises(exc) as info:
        client.generate_json("p", SCHEMA)
    assert KEY not in str(info.value)


def test_prompt_block_and_empty_output_are_content_blocked(server):
    client, _, _ = make(server, requests_per_minute=None)
    server["queue"] = [(200, {"promptFeedback": {"blockReason": "SAFETY"}})]
    with pytest.raises(ContentBlocked, match="SAFETY"):
        client.generate_json("p", SCHEMA)
    server["queue"] = [(200, {"candidates": [{"finishReason": "SAFETY", "content": {}}]})]
    with pytest.raises(ContentBlocked, match="SAFETY"):
        client.generate_json("p", SCHEMA)


@pytest.mark.parametrize("reply", [ok("not json at all"), ok("[1, 2]")])
def test_unusable_model_output_is_bad_output(server, reply):
    server["queue"] = [reply]
    client, _, _ = make(server, requests_per_minute=None)
    with pytest.raises(BadOutput):
        client.generate_json("p", SCHEMA)


def test_no_candidates_or_non_json_envelope_is_schema_changed(server):
    client, _, _ = make(server, requests_per_minute=None)
    server["queue"] = [(200, {"candidates": []})]
    with pytest.raises(SchemaChanged):
        client.generate_json("p", SCHEMA)
    server["queue"] = [(200, b"<html>")]
    with pytest.raises(SchemaChanged):
        client.generate_json("p", SCHEMA)


def test_timeout_is_unreachable(server):
    server["queue"] = [ok()]
    server["delay"] = 0.6
    client, _, _ = make(server, http=httpx.Client(timeout=0.2), requests_per_minute=None)
    with pytest.raises(Unreachable, match="timed out"):
        client.generate_json("p", SCHEMA)


def test_missing_key_raises_config_error_and_sends_nothing(server):
    server["queue"] = [ok()]
    client, _, _ = make(server, env={})
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        client.generate_json("p", SCHEMA)
    assert server["requests"] == []


def test_calls_are_spaced_to_the_configured_rate(server):
    server["queue"] = [ok()]
    client, sleeps, _ = make(server, requests_per_minute=15)
    client.generate_json("a", SCHEMA)
    client._spacer._clock = lambda: client._spacer._next - 1.0     # a second after the first call, well inside the 4 s gap
    client.generate_json("b", SCHEMA)
    assert sleeps and abs(sleeps[0] - 1.0) < 0.01


def test_retry_delay_and_daily_quota_parsers():
    err = too_many("21s")[1]["error"]
    assert retry_delay_s(err) == 21.0 and not is_daily_quota(err)
    assert retry_delay_s({}) is None and retry_delay_s({"details": [{"@type": "x.RetryInfo", "retryDelay": "soon"}]}) is None
    assert is_daily_quota(too_many("1s", "PerDay-X")[1]["error"])


def test_a_timeout_or_5xx_is_retried_once_after_a_backoff(server):
    server["queue"] = [(503, {"error": {"message": "overloaded"}}), ok()]
    client, sleeps, cfg = make(server, requests_per_minute=None)
    assert client.generate_json("p", SCHEMA).data == {"x": "y"} and len(server["requests"]) == 2 and cfg.policy.gemini.transient_backoff_s in sleeps


def test_when_the_first_model_keeps_failing_the_next_model_answers_and_is_named(server):
    cfg = load_config(CONFIG)
    first, second = cfg.policy.gemini.models[:2]
    server["queue"] = [(503, {"error": {"message": "overloaded"}}), ok({"x": "from the second"})]
    client, _, _ = make(server, requests_per_minute=None, models=[first, second], transient_retries=0)
    reply = client.generate_json("p", SCHEMA)
    assert reply.data == {"x": "from the second"} and reply.model == second and server["requests"][1]["path"].startswith(f"/v1beta/models/{second}:")


def test_a_used_up_quota_on_one_model_falls_back_but_a_bad_key_never_does(server):
    cfg = load_config(CONFIG)
    models = cfg.policy.gemini.models[:2]
    server["queue"] = [too_many("1s", quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier"), ok()]
    client, _, _ = make(server, requests_per_minute=None, models=models)
    assert client.generate_json("p", SCHEMA).model == models[1]
    server["requests"].clear(); server["queue"] = [(403, {"error": {"message": "no"}})]
    with pytest.raises(AuthRefused):
        client.generate_json("p", SCHEMA)
    assert len(server["requests"]) == 1


def test_when_every_model_fails_the_first_error_is_raised(server):
    models = load_config(CONFIG).policy.gemini.models[:2]
    server["queue"] = [too_many("1s", quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier")]
    client, _, _ = make(server, requests_per_minute=None, models=models)
    with pytest.raises(QuotaExhausted):
        client.generate_json("p", SCHEMA)
    assert len(server["requests"]) == 2
