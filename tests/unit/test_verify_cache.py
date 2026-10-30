"""The same claim twice answers from cache. The upstream services are local servers that count every request they receive."""

from concurrent.futures import ThreadPoolExecutor

from test_verify_api import POST_TEXT, gemini_reply, item, review, world  # noqa: F401  (world is a fixture)

from satyasutra import __version__
from satyasutra.cache import TTLCache
from satyasutra.config import load_config
from satyasutra.verdict_cache import VerdictCache

UPSTREAM = ("gemini", "/search")


def upstream_calls(state):
    return [c for c in state["calls"] if c == "gemini" or c.endswith("/search")]


def post(client, text=POST_TEXT):
    response = client.post("/verify", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_same_text_twice_returns_the_first_verdict_and_calls_nothing_upstream_again(world):
    client, store, state = world
    first = post(client)
    calls_after_first = len(upstream_calls(state))
    second = post(client)
    assert (first["from_cache"], second["from_cache"]) == (False, True) and calls_after_first == 2
    assert len(upstream_calls(state)) == calls_after_first, "a cache hit must not call Gemini or the Fact Check API"
    assert second["claims"] == first["claims"] and second["claims"][0]["ledger_seq"] == first["claims"][0]["ledger_seq"]
    assert store.head().seq == 1, "a hit must not write a second ledger row"
    assert second["made_at"] == first["made_at"] and second["age_s"] >= 0


def test_case_spacing_and_a_url_do_not_defeat_the_cache(world):
    client, store, state = world
    post(client)
    calls = len(upstream_calls(state))
    again = post(client, "  THE new bridge in the capital collapsed   this morning killing forty people https://t.co/abc ")
    assert again["from_cache"] is True and len(upstream_calls(state)) == calls


def test_a_different_text_is_a_miss(world):
    client, store, state = world
    post(client)
    state["gemini"] = gemini_reply([item("Another claim entirely about a different event in a faraway city")])
    other = post(client, "Another claim entirely about a different event in a faraway city")
    assert other["from_cache"] is False


def test_an_undecided_result_expires_sooner_than_a_decided_one(world):
    client, store, state = world
    cfg = state["cfg"].runtime.verify
    assert cfg.cache_ttl_undecided_s < cfg.cache_ttl_decided_s
    state["factcheck"] = review(POST_TEXT, "False")
    decided = post(client)
    assert decided["claims"][0]["label"] == "REFUTED"
    state["now"][0] += cfg.cache_ttl_undecided_s + 1
    assert post(client)["from_cache"] is True                       # still inside the decided TTL
    state["now"][0] += cfg.cache_ttl_decided_s
    assert post(client)["from_cache"] is False                      # past it

    state["factcheck"] = {}
    other = "Another claim entirely about a different event in a faraway city"
    state["gemini"] = gemini_reply([item(other)])
    assert post(client, other)["claims"][0]["label"] == "UNVERIFIABLE_YET"
    state["now"][0] += cfg.cache_ttl_undecided_s + 1
    assert post(client, other)["from_cache"] is False


def test_an_expired_entry_is_recomputed_and_writes_a_new_ledger_row_only_if_the_decision_changed(world):
    client, store, state = world
    cfg = state["cfg"].runtime.verify
    first = post(client)
    state["now"][0] += cfg.cache_ttl_undecided_s + 1
    again = post(client)
    assert again["from_cache"] is False and again["claims"][0]["ledger_seq"] == first["claims"][0]["ledger_seq"]   # same decision, same row (idempotent)
    state["factcheck"] = review(POST_TEXT, "False")
    state["now"][0] += cfg.cache_ttl_undecided_s + 1
    changed = post(client)
    assert changed["claims"][0]["label"] == "REFUTED" and changed["claims"][0]["ledger_seq"] > first["claims"][0]["ledger_seq"]


def test_a_failed_run_is_never_cached(world):
    client, store, state = world
    state["gemini_code"], state["gemini"] = 500, {"error": {"message": "boom"}}
    assert client.post("/verify", json={"text": POST_TEXT}).status_code == 502
    state["gemini_code"], state["gemini"] = 200, gemini_reply([item(POST_TEXT)])
    ok = post(client)
    assert ok["from_cache"] is False and ok["claims"]


def test_output_that_failed_validation_is_not_cached(world):
    client, store, state = world
    state["gemini"] = gemini_reply([item("a quote that is not in the post at all")])
    first = post(client)
    assert first["extraction"]["status"] == "invalid_output" and first["claims"] == []
    calls = len(upstream_calls(state))
    state["gemini"] = gemini_reply([item(POST_TEXT)])
    second = post(client)
    assert second["from_cache"] is False and second["claims"] and len(upstream_calls(state)) > calls


def test_a_post_without_claims_is_cached_so_opinions_do_not_cost_a_model_call_each_time(world):
    client, store, state = world
    state["gemini"] = gemini_reply([item(POST_TEXT, kind="opinion")])
    post(client)
    calls = len(upstream_calls(state))
    assert post(client)["from_cache"] is True and len(upstream_calls(state)) == calls


def test_identical_requests_arriving_together_make_one_upstream_call(world):
    client, store, state = world
    state["delay"] = 0.3
    with ThreadPoolExecutor(6) as pool:
        bodies = list(pool.map(lambda _: post(client), range(6)))
    assert state["calls"].count("gemini") == 1
    assert sorted(b["from_cache"] for b in bodies) == [False] + [True] * 5
    assert {b["claims"][0]["ledger_seq"] for b in bodies} == {1}


def test_a_hit_still_counts_toward_the_cluster_velocity(world):
    client, store, state = world
    post(client), post(client), post(client)
    [(cluster, _)] = state["fastclock"].top()
    assert cluster.size == 3


def test_decided_results_report_how_old_their_evidence_is(world):
    client, store, state = world
    state["factcheck"] = review(POST_TEXT, "False")
    body = post(client)
    assert body["claims"][0]["citations"] and body["evidence_age_s"] >= 0
    assert post(client)["evidence_age_s"] >= body["evidence_age_s"]


# the cache on its own ------------------------------------------------------------------------------------------------

def make(tmp_path, cfg, scope="ledger-a", version=__version__):
    return VerdictCache(TTLCache(tmp_path / "v.sqlite3"), cfg, version, scope=scope)


BODY = {"extraction": {"status": "ok"}, "claims": []}


def test_an_entry_made_under_another_config_code_version_or_ledger_is_a_miss(tmp_path):
    cfg = load_config(__import__("pathlib").Path(__file__).resolve().parents[2] / "config")
    make(tmp_path, cfg).get_or_compute("some text to check", lambda: BODY)
    assert make(tmp_path, cfg).get("some text to check") is not None
    assert make(tmp_path, cfg, version="other").get("some text to check") is None
    assert make(tmp_path, cfg, scope="ledger-b").get("some text to check") is None
    changed = cfg.model_copy(update={"checksum": "different"})
    assert make(tmp_path, changed).get("some text to check") is None
